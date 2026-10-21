//! Fixed-size CLOCK buffer pool. `SPEC.md` §5.3 (`IMPLEMENTATION_PLAN.md` 2.3).
//!
//! Each frame carries a `pin_count`, a `dirty` flag, and a `ref_bit` (second-chance bit).
//! The CLOCK hand skips pinned frames (`pin_count > 0`), clears `ref_bit` on the first
//! visit to an unpinned frame, and evicts an unpinned frame whose `ref_bit` is `false`
//! (writing it back to the underlying `PageStore` first if `dirty`).
//!
//! Per `SPEC.md` §5.3, attempting to evict when every frame is pinned is a bug, not a
//! recoverable condition: the pool panics rather than corrupting a pinned page.

use crate::{PageBuf, PageId, PageStore};
use privguard_core::{Error, Result, PAGE_SIZE};
use std::collections::HashMap;

#[derive(Debug)]
struct Frame {
    page_id: Option<PageId>,
    pin_count: u32,
    dirty: bool,
    ref_bit: bool,
    buf: Box<PageBuf>,
}

impl Frame {
    fn new() -> Self {
        Frame {
            page_id: None,
            pin_count: 0,
            dirty: false,
            ref_bit: false,
            buf: Box::new([0u8; PAGE_SIZE]),
        }
    }
}

/// Fixed-size page buffer pool with CLOCK eviction (`SPEC.md` §5.3).
#[derive(Debug)]
pub struct BufferPool<S: PageStore> {
    store: S,
    frames: Vec<Frame>,
    page_table: HashMap<PageId, usize>,
    hand: usize,
    evictions: u64,
}

impl<S: PageStore> BufferPool<S> {
    /// Create a buffer pool with `capacity` frames (`capacity` must be at least 1).
    pub fn new(store: S, capacity: usize) -> Self {
        assert!(capacity > 0, "buffer pool capacity must be at least 1");
        let frames = (0..capacity).map(|_| Frame::new()).collect();
        BufferPool {
            store,
            frames,
            page_table: HashMap::with_capacity(capacity),
            hand: 0,
            evictions: 0,
        }
    }

    pub fn capacity(&self) -> usize {
        self.frames.len()
    }

    pub fn evictions(&self) -> u64 {
        self.evictions
    }

    pub fn store(&self) -> &S {
        &self.store
    }

    pub fn into_store(mut self) -> Result<S> {
        self.flush_all()?;
        Ok(self.store)
    }

    /// Allocate a new zeroed page in the underlying store.
    pub fn allocate_page(&mut self) -> Result<PageId> {
        self.store.allocate_page()
    }

    /// Pin `page_id` in the pool and return a shared reference to its 8 KiB frame.
    pub fn pin_page(&mut self, page_id: PageId) -> Result<&PageBuf> {
        let idx = self.fetch_frame(page_id)?;
        Ok(&self.frames[idx].buf)
    }

    /// Pin `page_id` in the pool, mark the frame dirty, and return a mutable reference.
    pub fn pin_page_mut(&mut self, page_id: PageId) -> Result<&mut PageBuf> {
        let idx = self.fetch_frame(page_id)?;
        self.frames[idx].dirty = true;
        Ok(&mut self.frames[idx].buf)
    }

    /// Decrement `page_id`'s pin count and optionally mark the frame dirty.
    pub fn unpin_page(&mut self, page_id: PageId, mark_dirty: bool) -> Result<()> {
        let &idx = self
            .page_table
            .get(&page_id)
            .ok_or_else(|| Error::engine(format!("page {page_id} is not resident in pool")))?;
        let frame = &mut self.frames[idx];
        if frame.pin_count == 0 {
            return Err(Error::engine(format!(
                "cannot unpin page {page_id}: pin_count is already 0"
            )));
        }
        frame.pin_count -= 1;
        frame.dirty |= mark_dirty;
        Ok(())
    }

    /// Write a dirty resident page back to the underlying store without evicting it.
    pub fn flush_page(&mut self, page_id: PageId) -> Result<()> {
        if let Some(&idx) = self.page_table.get(&page_id) {
            let frame = &mut self.frames[idx];
            if frame.dirty {
                self.store.write_page(page_id, &frame.buf)?;
                frame.dirty = false;
            }
        }
        Ok(())
    }

    /// Write all dirty resident frames back to the underlying store.
    pub fn flush_all(&mut self) -> Result<()> {
        for frame in &mut self.frames {
            if let Some(pid) = frame.page_id {
                if frame.dirty {
                    self.store.write_page(pid, &frame.buf)?;
                    frame.dirty = false;
                }
            }
        }
        Ok(())
    }

    fn fetch_frame(&mut self, page_id: PageId) -> Result<usize> {
        if let Some(&idx) = self.page_table.get(&page_id) {
            let frame = &mut self.frames[idx];
            frame.pin_count += 1;
            frame.ref_bit = true;
            return Ok(idx);
        }

        let victim = self.select_victim();
        assert_eq!(
            self.frames[victim].pin_count, 0,
            "CLOCK selected a pinned frame for eviction"
        );

        if let Some(old_id) = self.frames[victim].page_id {
            if self.frames[victim].dirty {
                self.store.write_page(old_id, &self.frames[victim].buf)?;
                self.frames[victim].dirty = false;
            }
            self.page_table.remove(&old_id);
            self.evictions += 1;
        }

        let mut fresh = [0u8; PAGE_SIZE];
        self.store.read_page(page_id, &mut fresh)?;

        let frame = &mut self.frames[victim];
        frame.buf.copy_from_slice(&fresh);
        frame.page_id = Some(page_id);
        frame.pin_count = 1;
        frame.dirty = false;
        frame.ref_bit = true;
        self.page_table.insert(page_id, victim);
        Ok(victim)
    }

    fn select_victim(&mut self) -> usize {
        if let Some(empty) = self.frames.iter().position(|f| f.page_id.is_none()) {
            return empty;
        }
        if self.frames.iter().all(|f| f.pin_count > 0) {
            panic!(
                "buffer pool exhausted: all {} frames are pinned",
                self.frames.len()
            );
        }
        let n = self.frames.len();
        loop {
            let idx = self.hand;
            self.hand = (self.hand + 1) % n;
            let frame = &mut self.frames[idx];
            if frame.pin_count > 0 {
                continue;
            }
            if frame.ref_bit {
                frame.ref_bit = false;
                continue;
            }
            return idx;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{MemPageStore, PageKind, SlottedPage};
    use privguard_core::Rid;

    #[test]
    fn eviction_with_pool_smaller_than_file_flushes_dirty_pages() {
        let mut store = MemPageStore::new();
        for _ in 0..6 {
            store.allocate_page().unwrap();
        }
        // Pool has only 2 frames for a 6-page file.
        let mut pool = BufferPool::new(store, 2);

        let mut rids = Vec::new();
        for pid in 0..6u32 {
            let mut sp = SlottedPage::new(pid, PageKind::Heap);
            let rid = sp
                .insert(format!("tuple-on-page-{pid}").as_bytes())
                .unwrap();
            rids.push(rid);

            let buf = pool.pin_page_mut(pid).unwrap();
            buf.copy_from_slice(&sp.to_bytes());
            pool.unpin_page(pid, true).unwrap();
        }

        assert!(pool.evictions() >= 4);

        // Re-read every page through the 2-frame pool and verify the slotted tuples and CRC32C.
        for (pid, rid) in (0..6u32).zip(rids) {
            let buf = pool.pin_page(pid).unwrap();
            let sp = SlottedPage::from_bytes(buf).unwrap();
            assert_eq!(rid, Rid::new(pid, 0));
            assert_eq!(
                sp.get(rid.slot).unwrap(),
                Some(format!("tuple-on-page-{pid}").as_bytes())
            );
            pool.unpin_page(pid, false).unwrap();
        }
    }

    #[test]
    fn clock_second_chance_spares_recently_referenced_frame() {
        let mut store = MemPageStore::new();
        for _ in 0..3 {
            store.allocate_page().unwrap();
        }
        let mut pool = BufferPool::new(store, 2);

        pool.pin_page(0).unwrap();
        pool.unpin_page(0, false).unwrap();
        pool.pin_page(1).unwrap();
        pool.unpin_page(1, false).unwrap();

        // Clear page 0's ref_bit by simulating one sweep, then touch page 0 again so
        // page 0 has ref_bit = true and page 1 is evicted first when page 2 arrives.
        pool.frames[0].ref_bit = true;
        pool.frames[1].ref_bit = false;

        pool.pin_page(2).unwrap();
        pool.unpin_page(2, false).unwrap();

        assert!(pool.page_table.contains_key(&0));
        assert!(!pool.page_table.contains_key(&1));
        assert!(pool.page_table.contains_key(&2));
    }

    #[test]
    #[should_panic(expected = "buffer pool exhausted")]
    fn panics_when_all_frames_are_pinned() {
        let mut store = MemPageStore::new();
        store.allocate_page().unwrap();
        store.allocate_page().unwrap();
        let mut pool = BufferPool::new(store, 1);
        let _ = pool.pin_page(0).unwrap();
        // Frame 0 is still pinned; requesting page 1 must panic rather than evict it.
        let _ = pool.pin_page(1);
    }
}
