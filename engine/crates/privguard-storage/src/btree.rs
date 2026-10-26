//! B+ tree index over a `PageStore`. `SPEC.md` §5.4 (`IMPLEMENTATION_PLAN.md` 2.4).
//!
//! Keys are `i64`, payloads are packed [`Rid`]s. Entries are ordered by the pair
//! `(key, rid)`, so one key may map to many RIDs (a non-unique index such as
//! `visits.patient_id`) and every entry is still distinct.
//!
//! Layout (see `docs/phase2-design.md` §2): page 0 is the file header holding
//! `root | entries`; every other page is one node stored as a single tuple in a
//! [`SlottedPage`], so nodes get the page CRC for free.
//!
//! ```text
//! leaf      count u16 | next u32 | count × (key i64, rid u64)
//! internal  count u16 | child0 u32 | count × (key i64, rid u64, child u32)
//! ```
//!
//! Pages freed by a merge are not reclaimed: the workload is load-then-read.

use crate::layout::MAX_TUPLE_SIZE;
use crate::{PageBuf, PageId, PageKind, PageStore, SlottedPage};
use privguard_core::{Error, Result, Rid, PAGE_SIZE};

/// An index entry: `(key, packed rid)`.
type Entry = (i64, u64);

const LEAF_HEADER: usize = 2 + 4;
const LEAF_ENTRY: usize = 16;
const INTERNAL_HEADER: usize = 2 + 4;
const INTERNAL_ENTRY: usize = 20;

/// Most entries a leaf can hold in one page.
pub const MAX_LEAF_ENTRIES: usize = (MAX_TUPLE_SIZE - LEAF_HEADER) / LEAF_ENTRY;
/// Most separator keys an internal node can hold in one page.
pub const MAX_INTERNAL_KEYS: usize = (MAX_TUPLE_SIZE - INTERNAL_HEADER) / INTERNAL_ENTRY;

const _: () = assert!(MAX_LEAF_ENTRIES == 509);
const _: () = assert!(MAX_INTERNAL_KEYS == 407);
const _: () = assert!(LEAF_HEADER + MAX_LEAF_ENTRIES * LEAF_ENTRY <= PAGE_SIZE);

#[derive(Debug, Clone, Default)]
struct Leaf {
    entries: Vec<Entry>,
    /// Right sibling, 0 for none (page 0 is the file header, never a node).
    next: PageId,
}

#[derive(Debug, Clone)]
struct Internal {
    /// `keys.len() + 1` children; child `i` holds entries `< keys[i]`, child `i + 1` entries
    /// `>= keys[i]`.
    keys: Vec<Entry>,
    children: Vec<PageId>,
}

#[derive(Debug, Clone)]
enum Node {
    Leaf(Leaf),
    Internal(Internal),
}

fn put_entry(out: &mut Vec<u8>, e: Entry) {
    out.extend_from_slice(&e.0.to_le_bytes());
    out.extend_from_slice(&e.1.to_le_bytes());
}

fn get_entry(b: &[u8]) -> Entry {
    (
        i64::from_le_bytes(b[0..8].try_into().unwrap()),
        u64::from_le_bytes(b[8..16].try_into().unwrap()),
    )
}

impl Node {
    fn kind(&self) -> PageKind {
        match self {
            Node::Leaf(_) => PageKind::BTreeLeaf,
            Node::Internal(_) => PageKind::BTreeInternal,
        }
    }

    fn encode(&self) -> Vec<u8> {
        let mut out = Vec::new();
        match self {
            Node::Leaf(l) => {
                out.extend_from_slice(&(l.entries.len() as u16).to_le_bytes());
                out.extend_from_slice(&l.next.to_le_bytes());
                for &e in &l.entries {
                    put_entry(&mut out, e);
                }
            }
            Node::Internal(n) => {
                out.extend_from_slice(&(n.keys.len() as u16).to_le_bytes());
                out.extend_from_slice(&n.children[0].to_le_bytes());
                for (k, c) in n.keys.iter().zip(&n.children[1..]) {
                    put_entry(&mut out, *k);
                    out.extend_from_slice(&c.to_le_bytes());
                }
            }
        }
        out
    }

    fn decode(kind: PageKind, b: &[u8]) -> Result<Node> {
        let corrupt = || Error::engine("corrupt B+ tree node");
        if b.len() < LEAF_HEADER {
            return Err(corrupt());
        }
        let count = u16::from_le_bytes(b[0..2].try_into().unwrap()) as usize;
        let head = u32::from_le_bytes(b[2..6].try_into().unwrap());
        match kind {
            PageKind::BTreeLeaf => {
                if b.len() != LEAF_HEADER + count * LEAF_ENTRY {
                    return Err(corrupt());
                }
                let entries = b[LEAF_HEADER..]
                    .chunks_exact(LEAF_ENTRY)
                    .map(get_entry)
                    .collect();
                Ok(Node::Leaf(Leaf {
                    entries,
                    next: head,
                }))
            }
            PageKind::BTreeInternal => {
                if b.len() != INTERNAL_HEADER + count * INTERNAL_ENTRY {
                    return Err(corrupt());
                }
                let mut keys = Vec::with_capacity(count);
                let mut children = Vec::with_capacity(count + 1);
                children.push(head);
                for c in b[INTERNAL_HEADER..].chunks_exact(INTERNAL_ENTRY) {
                    keys.push(get_entry(&c[..16]));
                    children.push(u32::from_le_bytes(c[16..20].try_into().unwrap()));
                }
                Ok(Node::Internal(Internal { keys, children }))
            }
            _ => Err(corrupt()),
        }
    }
}

/// A B+ tree stored in one `PageStore`.
#[derive(Debug)]
pub struct BPlusTree<S: PageStore> {
    store: S,
    root: PageId,
    entries: u64,
    leaf_cap: usize,
    internal_cap: usize,
}

impl<S: PageStore> BPlusTree<S> {
    /// Open the tree in `store`, creating an empty one if the store is empty.
    pub fn open(store: S) -> Result<Self> {
        Self::open_with_caps(store, MAX_LEAF_ENTRIES, MAX_INTERNAL_KEYS)
    }

    /// Like [`open`](Self::open) with smaller node capacities, so tests can force splits and
    /// merges with a few keys. Capacities are not persisted: reopen with the ones used to build.
    pub fn open_with_caps(store: S, leaf_cap: usize, internal_cap: usize) -> Result<Self> {
        if !(4..=MAX_LEAF_ENTRIES).contains(&leaf_cap)
            || !(4..=MAX_INTERNAL_KEYS).contains(&internal_cap)
        {
            return Err(Error::engine("B+ tree node capacity out of range"));
        }
        let fresh = store.page_count() == 0;
        let mut tree = BPlusTree {
            store,
            root: 0,
            entries: 0,
            leaf_cap,
            internal_cap,
        };
        if fresh {
            tree.store.allocate_page()?; // page 0: file header
            tree.root = tree.alloc_node(&Node::Leaf(Leaf::default()))?;
            tree.write_meta()?;
        } else {
            let mut buf: PageBuf = [0u8; PAGE_SIZE];
            tree.store.read_page(0, &mut buf)?;
            let hdr = SlottedPage::from_bytes(&buf)?;
            if hdr.kind() != PageKind::FileHeader {
                return Err(Error::engine("page 0 is not a FileHeader page"));
            }
            let raw = hdr
                .get(0)?
                .filter(|r| r.len() == 12)
                .ok_or_else(|| Error::engine("corrupt B+ tree header"))?;
            tree.root = u32::from_le_bytes(raw[0..4].try_into().unwrap());
            tree.entries = u64::from_le_bytes(raw[4..12].try_into().unwrap());
        }
        Ok(tree)
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    pub fn into_store(self) -> S {
        self.store
    }

    /// Number of entries in the tree.
    pub fn len(&self) -> u64 {
        self.entries
    }

    pub fn is_empty(&self) -> bool {
        self.entries == 0
    }

    fn write_meta(&mut self) -> Result<()> {
        let mut meta = Vec::with_capacity(12);
        meta.extend_from_slice(&self.root.to_le_bytes());
        meta.extend_from_slice(&self.entries.to_le_bytes());
        let mut hdr = SlottedPage::new(0, PageKind::FileHeader);
        hdr.insert(&meta)?;
        self.store.write_page(0, &hdr.to_bytes())
    }

    fn read_node(&self, id: PageId) -> Result<Node> {
        if id == 0 {
            return Err(Error::engine("page 0 is the file header, not a node"));
        }
        let mut buf: PageBuf = [0u8; PAGE_SIZE];
        self.store.read_page(id, &mut buf)?;
        let page = SlottedPage::from_bytes(&buf)?;
        let raw = page
            .get(0)?
            .ok_or_else(|| Error::engine("empty B+ tree node page"))?;
        Node::decode(page.kind(), raw)
    }

    fn write_node(&mut self, id: PageId, node: &Node) -> Result<()> {
        let mut page = SlottedPage::new(id, node.kind());
        page.insert(&node.encode())?;
        self.store.write_page(id, &page.to_bytes())
    }

    fn alloc_node(&mut self, node: &Node) -> Result<PageId> {
        let id = self.store.allocate_page()?;
        self.write_node(id, node)?;
        Ok(id)
    }

    /// Insert `(key, rid)`. Returns `false` (and changes nothing) if it is already present.
    pub fn insert(&mut self, key: i64, rid: Rid) -> Result<bool> {
        let entry = (key, rid.to_u64());
        let (inserted, split) = self.insert_rec(self.root, entry)?;
        if let Some((sep, right)) = split {
            let new_root = Node::Internal(Internal {
                keys: vec![sep],
                children: vec![self.root, right],
            });
            self.root = self.alloc_node(&new_root)?;
        }
        if inserted {
            self.entries += 1;
            self.write_meta()?;
        }
        Ok(inserted)
    }

    fn insert_rec(&mut self, page: PageId, e: Entry) -> Result<(bool, Option<(Entry, PageId)>)> {
        match self.read_node(page)? {
            Node::Leaf(mut leaf) => {
                let pos = match leaf.entries.binary_search(&e) {
                    Ok(_) => return Ok((false, None)),
                    Err(p) => p,
                };
                leaf.entries.insert(pos, e);
                if leaf.entries.len() <= self.leaf_cap {
                    self.write_node(page, &Node::Leaf(leaf))?;
                    return Ok((true, None));
                }
                let right_entries = leaf.entries.split_off(leaf.entries.len() / 2);
                let sep = right_entries[0];
                let right = Node::Leaf(Leaf {
                    entries: right_entries,
                    next: leaf.next,
                });
                let right_id = self.alloc_node(&right)?;
                leaf.next = right_id;
                self.write_node(page, &Node::Leaf(leaf))?;
                Ok((true, Some((sep, right_id))))
            }
            Node::Internal(mut node) => {
                let idx = node.keys.partition_point(|s| *s <= e);
                let (inserted, split) = self.insert_rec(node.children[idx], e)?;
                let Some((sep, right)) = split else {
                    return Ok((inserted, None));
                };
                node.keys.insert(idx, sep);
                node.children.insert(idx + 1, right);
                if node.keys.len() <= self.internal_cap {
                    self.write_node(page, &Node::Internal(node))?;
                    return Ok((inserted, None));
                }
                let mid = node.keys.len() / 2;
                let up = node.keys[mid];
                let right_keys = node.keys.split_off(mid + 1);
                node.keys.pop();
                let right_children = node.children.split_off(mid + 1);
                let right_id = self.alloc_node(&Node::Internal(Internal {
                    keys: right_keys,
                    children: right_children,
                }))?;
                self.write_node(page, &Node::Internal(node))?;
                Ok((inserted, Some((up, right_id))))
            }
        }
    }

    /// Remove `(key, rid)`. Returns whether it was present.
    pub fn delete(&mut self, key: i64, rid: Rid) -> Result<bool> {
        let (removed, _) = self.delete_rec(self.root, (key, rid.to_u64()), true)?;
        if !removed {
            return Ok(false);
        }
        self.entries -= 1;
        // A root that lost its last separator is replaced by its only child.
        if let Node::Internal(root) = self.read_node(self.root)? {
            if root.keys.is_empty() {
                self.root = root.children[0];
            }
        }
        self.write_meta()?;
        Ok(true)
    }

    /// Returns `(removed, node_underflowed)`.
    fn delete_rec(&mut self, page: PageId, e: Entry, is_root: bool) -> Result<(bool, bool)> {
        match self.read_node(page)? {
            Node::Leaf(mut leaf) => {
                let Ok(pos) = leaf.entries.binary_search(&e) else {
                    return Ok((false, false));
                };
                leaf.entries.remove(pos);
                let under = !is_root && leaf.entries.len() < self.leaf_cap / 2;
                self.write_node(page, &Node::Leaf(leaf))?;
                Ok((true, under))
            }
            Node::Internal(mut node) => {
                let idx = node.keys.partition_point(|s| *s <= e);
                let (removed, child_under) = self.delete_rec(node.children[idx], e, false)?;
                if !child_under {
                    return Ok((removed, false));
                }
                self.rebalance(&mut node, idx)?;
                let under = !is_root && node.keys.len() < self.internal_cap / 2;
                self.write_node(page, &Node::Internal(node))?;
                Ok((removed, under))
            }
        }
    }

    /// Fix the underfull child `idx` of `parent` by merging it with, or redistributing
    /// entries to, an adjacent sibling. Mutates `parent` in memory; the caller writes it.
    fn rebalance(&mut self, parent: &mut Internal, idx: usize) -> Result<()> {
        let li = if idx > 0 { idx - 1 } else { idx };
        let (lid, rid) = (parent.children[li], parent.children[li + 1]);
        match (self.read_node(lid)?, self.read_node(rid)?) {
            (Node::Leaf(mut l), Node::Leaf(r)) => {
                l.entries.extend(r.entries);
                if l.entries.len() <= self.leaf_cap {
                    l.next = r.next;
                    parent.keys.remove(li);
                    parent.children.remove(li + 1);
                    self.write_node(lid, &Node::Leaf(l))?;
                } else {
                    let right_entries = l.entries.split_off(l.entries.len() / 2);
                    parent.keys[li] = right_entries[0];
                    let right = Leaf {
                        entries: right_entries,
                        next: r.next,
                    };
                    self.write_node(lid, &Node::Leaf(l))?;
                    self.write_node(rid, &Node::Leaf(right))?;
                }
            }
            (Node::Internal(mut l), Node::Internal(r)) => {
                l.keys.push(parent.keys[li]);
                l.keys.extend(r.keys);
                l.children.extend(r.children);
                if l.keys.len() <= self.internal_cap {
                    parent.keys.remove(li);
                    parent.children.remove(li + 1);
                    self.write_node(lid, &Node::Internal(l))?;
                } else {
                    let mid = l.keys.len() / 2;
                    parent.keys[li] = l.keys[mid];
                    let right_keys = l.keys.split_off(mid + 1);
                    l.keys.pop();
                    let right_children = l.children.split_off(mid + 1);
                    self.write_node(lid, &Node::Internal(l))?;
                    self.write_node(
                        rid,
                        &Node::Internal(Internal {
                            keys: right_keys,
                            children: right_children,
                        }),
                    )?;
                }
            }
            _ => return Err(Error::engine("B+ tree siblings at different depths")),
        }
        Ok(())
    }

    /// Leaf that holds, or would hold, `target`.
    fn find_leaf(&self, target: Entry) -> Result<Leaf> {
        let mut page = self.root;
        loop {
            match self.read_node(page)? {
                Node::Leaf(l) => return Ok(l),
                Node::Internal(n) => {
                    page = n.children[n.keys.partition_point(|s| *s <= target)];
                }
            }
        }
    }

    /// Every RID stored under `key`, in RID order.
    pub fn lookup(&self, key: i64) -> Result<Vec<Rid>> {
        Ok(self
            .range(Some(key), Some(key))?
            .into_iter()
            .map(|(_, r)| r)
            .collect())
    }

    /// Entries with `lo <= key <= hi` in `(key, rid)` order; `None` leaves that side open.
    pub fn range(&self, lo: Option<i64>, hi: Option<i64>) -> Result<Vec<(i64, Rid)>> {
        let start = (lo.unwrap_or(i64::MIN), 0);
        let mut leaf = self.find_leaf(start)?;
        let mut out = Vec::new();
        loop {
            let from = leaf.entries.partition_point(|e| *e < start);
            for &(k, r) in &leaf.entries[from..] {
                if hi.is_some_and(|h| k > h) {
                    return Ok(out);
                }
                out.push((k, Rid::from_u64(r)));
            }
            if leaf.next == 0 {
                return Ok(out);
            }
            match self.read_node(leaf.next)? {
                Node::Leaf(l) => leaf = l,
                Node::Internal(_) => {
                    return Err(Error::engine("leaf chain reaches an internal node"))
                }
            }
        }
    }

    /// Verify the structural invariants: ordering, separator bounds, uniform depth, minimum
    /// occupancy, the leaf chain and the entry count. Used by tests and `debug` tooling.
    pub fn check(&self) -> Result<()> {
        let bad = |m: &str| Error::engine(format!("B+ tree invariant violated: {m}"));
        let mut leaves = Vec::new();
        let depth = self.check_node(self.root, None, None, true, &mut leaves)?;
        let _ = depth;
        let mut total = 0u64;
        for (i, (id, n)) in leaves.iter().enumerate() {
            total += *n as u64;
            let expect_next = leaves.get(i + 1).map_or(0, |(next, _)| *next);
            match self.read_node(*id)? {
                Node::Leaf(l) if l.next == expect_next => {}
                _ => return Err(bad("leaf chain does not follow tree order")),
            }
        }
        if total != self.entries {
            return Err(bad("entry count does not match the leaves"));
        }
        Ok(())
    }

    /// Returns the subtree depth; appends `(page, entry_count)` for each leaf in order.
    fn check_node(
        &self,
        page: PageId,
        lo: Option<Entry>,
        hi: Option<Entry>,
        is_root: bool,
        leaves: &mut Vec<(PageId, usize)>,
    ) -> Result<usize> {
        let bad = |m: &str| Error::engine(format!("B+ tree invariant violated: {m}"));
        let in_bounds = |e: &Entry| lo.is_none_or(|l| *e >= l) && hi.is_none_or(|h| *e < h);
        match self.read_node(page)? {
            Node::Leaf(l) => {
                if !l.entries.windows(2).all(|w| w[0] < w[1]) {
                    return Err(bad("leaf entries not strictly ascending"));
                }
                if !l.entries.iter().all(in_bounds) {
                    return Err(bad("leaf entry outside its separator bounds"));
                }
                if l.entries.len() > self.leaf_cap
                    || (!is_root && l.entries.len() < self.leaf_cap / 2)
                {
                    return Err(bad("leaf occupancy out of range"));
                }
                leaves.push((page, l.entries.len()));
                Ok(1)
            }
            Node::Internal(n) => {
                if n.children.len() != n.keys.len() + 1 || n.keys.is_empty() {
                    return Err(bad("internal node shape"));
                }
                if !n.keys.windows(2).all(|w| w[0] < w[1]) || !n.keys.iter().all(in_bounds) {
                    return Err(bad("internal separators out of order or bounds"));
                }
                if n.keys.len() > self.internal_cap
                    || (!is_root && n.keys.len() < self.internal_cap / 2)
                {
                    return Err(bad("internal occupancy out of range"));
                }
                let mut depth = None;
                for (i, &child) in n.children.iter().enumerate() {
                    let clo = if i == 0 { lo } else { Some(n.keys[i - 1]) };
                    let chi = if i == n.keys.len() {
                        hi
                    } else {
                        Some(n.keys[i])
                    };
                    let d = self.check_node(child, clo, chi, false, leaves)?;
                    if *depth.get_or_insert(d) != d {
                        return Err(bad("leaves at different depths"));
                    }
                }
                Ok(depth.unwrap() + 1)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::MemPageStore;
    use std::collections::BTreeSet;

    fn rid(n: u64) -> Rid {
        Rid::from_u64(n)
    }

    fn small() -> BPlusTree<MemPageStore> {
        BPlusTree::open_with_caps(MemPageStore::new(), 4, 4).unwrap()
    }

    /// Small deterministic generator so the tests need no `rand`.
    struct Lcg(u64);
    impl Lcg {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 33
        }
    }

    #[test]
    fn node_capacities_follow_from_the_page_size() {
        assert_eq!(MAX_LEAF_ENTRIES, 509);
        assert_eq!(MAX_INTERNAL_KEYS, 407);
    }

    #[test]
    fn empty_tree_finds_nothing() {
        let t = BPlusTree::open(MemPageStore::new()).unwrap();
        assert!(t.is_empty());
        assert_eq!(t.lookup(1).unwrap(), vec![]);
        assert_eq!(t.range(None, None).unwrap(), vec![]);
        t.check().unwrap();
    }

    #[test]
    fn point_lookup_and_range_scan_after_splits() {
        let mut t = small();
        for k in (0..200i64).rev() {
            assert!(t.insert(k, rid(k as u64 * 10)).unwrap());
            if k % 8 == 0 {
                t.check().unwrap();
            }
        }
        assert_eq!(t.len(), 200);
        for k in [0, 1, 99, 199] {
            assert_eq!(t.lookup(k).unwrap(), vec![rid(k as u64 * 10)]);
        }
        assert_eq!(t.lookup(200).unwrap(), vec![]);
        assert_eq!(t.lookup(-1).unwrap(), vec![]);

        let r = t.range(Some(50), Some(59)).unwrap();
        let keys: Vec<i64> = r.iter().map(|(k, _)| *k).collect();
        assert_eq!(keys, (50..=59).collect::<Vec<_>>());

        assert_eq!(t.range(Some(195), None).unwrap().len(), 5);
        assert_eq!(t.range(None, Some(4)).unwrap().len(), 5);
        assert_eq!(t.range(None, None).unwrap().len(), 200);
        assert_eq!(t.range(Some(10), Some(5)).unwrap(), vec![]);
    }

    #[test]
    fn duplicate_keys_keep_every_rid() {
        let mut t = small();
        for r in 0..30u64 {
            assert!(t.insert(7, rid(r)).unwrap());
        }
        t.insert(6, rid(1)).unwrap();
        t.insert(8, rid(1)).unwrap();
        t.check().unwrap();
        let got: Vec<u64> = t.lookup(7).unwrap().iter().map(|r| r.to_u64()).collect();
        assert_eq!(got, (0..30).collect::<Vec<_>>());
        // Exact duplicate is refused.
        assert!(!t.insert(7, rid(3)).unwrap());
        assert_eq!(t.len(), 32);
    }

    #[test]
    fn delete_removes_only_the_named_entry() {
        let mut t = small();
        t.insert(1, rid(1)).unwrap();
        t.insert(1, rid(2)).unwrap();
        assert!(t.delete(1, rid(1)).unwrap());
        assert!(!t.delete(1, rid(1)).unwrap());
        assert!(!t.delete(2, rid(1)).unwrap());
        assert_eq!(t.lookup(1).unwrap(), vec![rid(2)]);
    }

    #[test]
    fn deleting_everything_merges_back_to_an_empty_root() {
        let mut t = small();
        for k in 0..300i64 {
            t.insert(k, rid(k as u64)).unwrap();
        }
        for k in 0..300i64 {
            assert!(t.delete(k, rid(k as u64)).unwrap(), "key {k}");
            if k % 8 == 0 {
                t.check().unwrap();
            }
        }
        assert!(t.is_empty());
        t.check().unwrap();
        assert_eq!(t.range(None, None).unwrap(), vec![]);
        // And it is still usable.
        t.insert(5, rid(5)).unwrap();
        assert_eq!(t.lookup(5).unwrap(), vec![rid(5)]);
    }

    #[test]
    fn random_workload_matches_a_btreeset_model() {
        let mut t = small();
        let mut model = BTreeSet::new();
        let mut rng = Lcg(42);
        for step in 0..4000 {
            let key = (rng.next() % 150) as i64;
            let r = rng.next() % 4;
            if rng.next().is_multiple_of(3) {
                let want = model.remove(&(key, r));
                assert_eq!(t.delete(key, rid(r)).unwrap(), want);
            } else {
                let want = model.insert((key, r));
                assert_eq!(t.insert(key, rid(r)).unwrap(), want);
            }
            if step % 50 == 0 {
                t.check().unwrap();
            }
        }
        t.check().unwrap();
        let got: Vec<(i64, u64)> = t
            .range(None, None)
            .unwrap()
            .into_iter()
            .map(|(k, r)| (k, r.to_u64()))
            .collect();
        assert_eq!(got, model.iter().copied().collect::<Vec<_>>());
        assert_eq!(t.len(), model.len() as u64);
    }

    #[test]
    fn tree_grows_to_several_levels_at_full_capacity() {
        let mut t = BPlusTree::open(MemPageStore::new()).unwrap();
        for k in 0..2000i64 {
            t.insert(k * 3, rid(k as u64)).unwrap();
        }
        t.check().unwrap();
        assert_eq!(t.lookup(1500).unwrap(), vec![rid(500)]);
        assert_eq!(t.range(Some(30), Some(60)).unwrap().len(), 11);
    }

    #[test]
    fn reopening_the_store_finds_the_same_tree() {
        let mut t = small();
        for k in 0..100i64 {
            t.insert(k, rid(k as u64)).unwrap();
        }
        let store = t.into_store();
        let t = BPlusTree::open_with_caps(store, 4, 4).unwrap();
        assert_eq!(t.len(), 100);
        t.check().unwrap();
        assert_eq!(t.lookup(42).unwrap(), vec![rid(42)]);
    }

    #[test]
    fn a_flipped_byte_in_a_node_is_detected() {
        let mut t = small();
        for k in 0..20i64 {
            t.insert(k, rid(k as u64)).unwrap();
        }
        let mut store = t.into_store();
        let mut buf = [0u8; PAGE_SIZE];
        store.read_page(1, &mut buf).unwrap();
        buf[100] ^= 0xFF;
        store.write_page(1, &buf).unwrap();
        let t = BPlusTree::open_with_caps(store, 4, 4).unwrap();
        assert!(t.range(None, None).is_err());
    }

    #[test]
    fn capacities_below_four_are_refused() {
        assert!(BPlusTree::open_with_caps(MemPageStore::new(), 3, 4).is_err());
        assert!(BPlusTree::open_with_caps(MemPageStore::new(), 4, 3).is_err());
    }
}
