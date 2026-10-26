//! Heap file over a `PageStore`. `SPEC.md` §5.2 (`IMPLEMENTATION_PLAN.md` 2.2).
//!
//! Pages are numbered from 0:
//! - Page 0 is `PageKind::FileHeader` and stores `(row_count: u64, fsm_root: u32)` plus
//!   a per-page free-space directory (`u16` free bytes for each heap page `1..N`).
//! - Pages `1..N` are `PageKind::Heap` slotted pages holding variable-length tuples.
//! - Sequential scan walks heap pages `1..N` in ascending page and slot order, yielding
//!   live `(Rid, Vec<u8>)` pairs in deterministic physical order (`SPEC.md` §4.2).

use crate::layout::MAX_TUPLE_SIZE;
use crate::{PageBuf, PageId, PageKind, PageStore, SlottedPage};
use privguard_core::{Error, Result, Rid, PAGE_SIZE};

#[derive(Debug, Clone, Default)]
struct FileHeaderMeta {
    row_count: u64,
    fsm_root: PageId,
    /// Free bytes for heap pages `1..=free_bytes.len()`.
    free_bytes: Vec<u16>,
}

impl FileHeaderMeta {
    fn encode(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(12 + self.free_bytes.len() * 2);
        out.extend_from_slice(&self.row_count.to_le_bytes());
        out.extend_from_slice(&self.fsm_root.to_le_bytes());
        for &fb in &self.free_bytes {
            out.extend_from_slice(&fb.to_le_bytes());
        }
        out
    }

    fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.len() < 12 || !(bytes.len() - 12).is_multiple_of(2) {
            return Err(Error::engine("corrupt heap file header metadata"));
        }
        let row_count = u64::from_le_bytes(bytes[0..8].try_into().unwrap());
        let fsm_root = u32::from_le_bytes(bytes[8..12].try_into().unwrap());
        let mut free_bytes = Vec::with_capacity((bytes.len() - 12) / 2);
        for chunk in bytes[12..].chunks_exact(2) {
            free_bytes.push(u16::from_le_bytes(chunk.try_into().unwrap()));
        }
        Ok(FileHeaderMeta {
            row_count,
            fsm_root,
            free_bytes,
        })
    }

    fn refresh_fsm_root(&mut self) {
        self.fsm_root = self
            .free_bytes
            .iter()
            .enumerate()
            .find(|(_, &fb)| fb > 0)
            .map(|(idx, _)| (idx as u32) + 1)
            .unwrap_or(0);
    }
}

/// A table heap file backed by any `PageStore`.
#[derive(Debug)]
pub struct HeapFile<S: PageStore> {
    store: S,
}

impl<S: PageStore> HeapFile<S> {
    /// Open an existing heap file, or initialise Page 0 if the store is empty.
    pub fn open(mut store: S) -> Result<Self> {
        if store.page_count() == 0 {
            let pid = store.allocate_page()?;
            debug_assert_eq!(pid, 0);
            let mut hdr = SlottedPage::new(0, PageKind::FileHeader);
            let meta = FileHeaderMeta::default();
            hdr.insert(&meta.encode())?;
            store.write_page(0, &hdr.to_bytes())?;
        } else {
            let mut buf: PageBuf = [0u8; PAGE_SIZE];
            store.read_page(0, &mut buf)?;
            let hdr = SlottedPage::from_bytes(&buf)?;
            if hdr.kind() != PageKind::FileHeader {
                return Err(Error::engine("page 0 is not a FileHeader page"));
            }
            let raw = hdr
                .get(0)?
                .ok_or_else(|| Error::engine("missing FileHeader metadata slot"))?;
            let _ = FileHeaderMeta::decode(raw)?;
        }
        Ok(HeapFile { store })
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    pub fn into_store(self) -> S {
        self.store
    }

    fn read_meta(&self) -> Result<FileHeaderMeta> {
        let mut buf: PageBuf = [0u8; PAGE_SIZE];
        self.store.read_page(0, &mut buf)?;
        let hdr = SlottedPage::from_bytes(&buf)?;
        let raw = hdr
            .get(0)?
            .ok_or_else(|| Error::engine("missing FileHeader metadata slot"))?;
        FileHeaderMeta::decode(raw)
    }

    fn write_meta(&mut self, meta: &FileHeaderMeta) -> Result<()> {
        let mut hdr = SlottedPage::new(0, PageKind::FileHeader);
        hdr.insert(&meta.encode())?;
        self.store.write_page(0, &hdr.to_bytes())
    }

    fn read_heap_page(&self, page_id: PageId) -> Result<SlottedPage> {
        if page_id == 0 {
            return Err(Error::engine("page 0 is the file header, not a heap page"));
        }
        let mut buf: PageBuf = [0u8; PAGE_SIZE];
        self.store.read_page(page_id, &mut buf)?;
        let page = SlottedPage::from_bytes(&buf)?;
        if page.kind() != PageKind::Heap {
            return Err(Error::engine(format!(
                "page {page_id} has kind {:?}, expected Heap",
                page.kind()
            )));
        }
        Ok(page)
    }

    /// Number of live tuples in the heap file (stored in Page 0).
    pub fn row_count(&self) -> Result<u64> {
        Ok(self.read_meta()?.row_count)
    }

    /// First candidate heap page with free space in the FSM, or 0 if none.
    pub fn fsm_root(&self) -> Result<PageId> {
        Ok(self.read_meta()?.fsm_root)
    }

    /// Insert a tuple into the first heap page that can fit it, or a newly allocated page.
    pub fn insert(&mut self, tuple: &[u8]) -> Result<Rid> {
        if tuple.len() > MAX_TUPLE_SIZE {
            return Err(Error::engine("tuple exceeds MAX_TUPLE_SIZE"));
        }
        let mut meta = self.read_meta()?;
        let mut chosen: Option<SlottedPage> = None;

        for (idx, &fb) in meta.free_bytes.iter().enumerate() {
            if (fb as usize) >= tuple.len() {
                let pid = (idx as u32) + 1;
                let page = self.read_heap_page(pid)?;
                if page.can_fit(tuple.len()) {
                    chosen = Some(page);
                    break;
                }
            }
        }

        let mut page = match chosen {
            Some(p) => p,
            None => {
                let pid = self.store.allocate_page()?;
                let p = SlottedPage::new(pid, PageKind::Heap);
                meta.free_bytes.push(p.total_free_space() as u16);
                p
            }
        };

        let rid = page.insert(tuple)?;
        self.store.write_page(rid.page_id, &page.to_bytes())?;

        meta.free_bytes[(rid.page_id - 1) as usize] = page.total_free_space() as u16;
        meta.row_count += 1;
        meta.refresh_fsm_root();
        self.write_meta(&meta)?;

        Ok(rid)
    }

    /// Point lookup by `Rid`. Returns `Ok(None)` if the slot was deleted.
    pub fn get(&self, rid: Rid) -> Result<Option<Vec<u8>>> {
        if rid.page_id == 0 || rid.page_id >= self.store.page_count() {
            return Err(Error::engine(format!(
                "invalid heap RID page {}",
                rid.page_id
            )));
        }
        let page = self.read_heap_page(rid.page_id)?;
        Ok(page.get(rid.slot)?.map(|s| s.to_vec()))
    }

    /// Delete the tuple at `rid`. Returns `Ok(true)` if a live tuple was deleted.
    pub fn delete(&mut self, rid: Rid) -> Result<bool> {
        if rid.page_id == 0 || rid.page_id >= self.store.page_count() {
            return Err(Error::engine(format!(
                "invalid heap RID page {}",
                rid.page_id
            )));
        }
        let mut page = self.read_heap_page(rid.page_id)?;
        if !page.delete(rid.slot)? {
            return Ok(false);
        }
        self.store.write_page(rid.page_id, &page.to_bytes())?;

        let mut meta = self.read_meta()?;
        meta.free_bytes[(rid.page_id - 1) as usize] = page.total_free_space() as u16;
        meta.row_count = meta.row_count.saturating_sub(1);
        meta.refresh_fsm_root();
        self.write_meta(&meta)?;
        Ok(true)
    }

    /// Sequential scan over all heap pages in ascending `(page_id, slot)` order.
    pub fn scan(&self) -> Result<Vec<(Rid, Vec<u8>)>> {
        let mut out = Vec::new();
        for pid in 1..self.store.page_count() {
            let page = self.read_heap_page(pid)?;
            for (rid, bytes) in page.iter_live() {
                out.push((rid, bytes.to_vec()));
            }
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::MemPageStore;

    #[test]
    fn page_zero_is_file_header_and_survives_reopen() {
        let mut hf = HeapFile::open(MemPageStore::new()).unwrap();
        assert_eq!(hf.row_count().unwrap(), 0);
        let r0 = hf.insert(b"row-0").unwrap();
        let r1 = hf.insert(b"row-1").unwrap();
        assert_eq!(r0.page_id, 1);
        assert_eq!(r1.page_id, 1);
        assert_eq!(hf.row_count().unwrap(), 2);

        let store = hf.into_store();
        let reopened = HeapFile::open(store).unwrap();
        assert_eq!(reopened.row_count().unwrap(), 2);
        assert_eq!(reopened.get(r0).unwrap(), Some(b"row-0".to_vec()));
        assert_eq!(reopened.get(r1).unwrap(), Some(b"row-1".to_vec()));
    }

    #[test]
    fn sequential_scan_spans_multiple_pages_and_skips_deleted_slots() {
        let mut hf = HeapFile::open(MemPageStore::new()).unwrap();
        let payload_a = vec![b'a'; 3000];
        let payload_b = vec![b'b'; 3000];
        let payload_c = vec![b'c'; 3000];
        let payload_d = vec![b'd'; 3000];

        let r0 = hf.insert(&payload_a).unwrap();
        let r1 = hf.insert(&payload_b).unwrap();
        // Third 3000-byte tuple spills onto heap page 2.
        let r2 = hf.insert(&payload_c).unwrap();
        let r3 = hf.insert(&payload_d).unwrap();
        assert_eq!(
            (r0.page_id, r1.page_id, r2.page_id, r3.page_id),
            (1, 1, 2, 2)
        );

        // Delete r1 on page 1; scan must yield r0, r2, r3 in RID order.
        assert!(hf.delete(r1).unwrap());
        assert_eq!(hf.row_count().unwrap(), 3);
        let scanned: Vec<Rid> = hf.scan().unwrap().into_iter().map(|(r, _)| r).collect();
        assert_eq!(scanned, vec![r0, r2, r3]);

        // Inserting another 3000-byte tuple reuses the reclaimed space on page 1 via FSM.
        let r4 = hf.insert(&payload_b).unwrap();
        assert_eq!(r4.page_id, 1);
        assert_eq!(hf.row_count().unwrap(), 4);
    }
}
