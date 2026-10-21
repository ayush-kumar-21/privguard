//! The page I/O seam. Phase 0 freeze.
//!
//! The buffer pool and B+ tree talk to storage only through [`PageStore`], so they
//! can be unit-tested over [`MemPageStore`] and later run over the real file-backed
//! store without changes.

use privguard_core::{Error, Result, PAGE_SIZE};

/// Page number within one store; page 0 is the file header (`SPEC.md` A5.1).
pub type PageId = u32;

/// One raw page image.
pub type PageBuf = [u8; PAGE_SIZE];

/// Read and write whole pages by id.
///
/// Implementations do no caching and no checksum verification; that belongs to the
/// buffer pool and page layers above.
pub trait PageStore {
    /// Copy page `id` into `buf`. Errors if `id` was never allocated.
    fn read_page(&self, id: PageId, buf: &mut PageBuf) -> Result<()>;

    /// Overwrite page `id`. Errors if `id` was never allocated.
    fn write_page(&mut self, id: PageId, buf: &PageBuf) -> Result<()>;

    /// Append a zeroed page and return its id (ids are dense, starting at 0).
    fn allocate_page(&mut self) -> Result<PageId>;

    /// Number of allocated pages.
    fn page_count(&self) -> u32;
}

/// `PageStore` held entirely in memory.
#[derive(Debug, Default)]
pub struct MemPageStore {
    pages: Vec<Box<PageBuf>>,
}

impl MemPageStore {
    pub fn new() -> Self {
        Self::default()
    }

    fn missing(id: PageId) -> Error {
        Error::engine(format!("page {id} out of range"))
    }
}

impl PageStore for MemPageStore {
    fn read_page(&self, id: PageId, buf: &mut PageBuf) -> Result<()> {
        let page = self
            .pages
            .get(id as usize)
            .ok_or_else(|| Self::missing(id))?;
        buf.copy_from_slice(&page[..]);
        Ok(())
    }

    fn write_page(&mut self, id: PageId, buf: &PageBuf) -> Result<()> {
        let page = self
            .pages
            .get_mut(id as usize)
            .ok_or_else(|| Self::missing(id))?;
        page.copy_from_slice(&buf[..]);
        Ok(())
    }

    fn allocate_page(&mut self) -> Result<PageId> {
        let id = u32::try_from(self.pages.len()).map_err(|_| Error::engine("page id overflow"))?;
        self.pages.push(Box::new([0u8; PAGE_SIZE]));
        Ok(id)
    }

    fn page_count(&self) -> u32 {
        self.pages.len() as u32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allocate_returns_dense_ids_and_zeroed_pages() {
        let mut s = MemPageStore::new();
        assert_eq!(s.page_count(), 0);
        assert_eq!(s.allocate_page().unwrap(), 0);
        assert_eq!(s.allocate_page().unwrap(), 1);
        assert_eq!(s.page_count(), 2);
        let mut buf = [0xFFu8; PAGE_SIZE];
        s.read_page(1, &mut buf).unwrap();
        assert!(buf.iter().all(|&b| b == 0));
    }

    #[test]
    fn write_then_read_round_trips_and_pages_are_independent() {
        let mut s = MemPageStore::new();
        let a = s.allocate_page().unwrap();
        let b = s.allocate_page().unwrap();
        let mut page = [0u8; PAGE_SIZE];
        page[0] = 7;
        page[PAGE_SIZE - 1] = 9;
        s.write_page(a, &page).unwrap();

        let mut out = [0u8; PAGE_SIZE];
        s.read_page(a, &mut out).unwrap();
        assert_eq!(out[..], page[..]);
        s.read_page(b, &mut out).unwrap();
        assert!(out.iter().all(|&x| x == 0));
    }

    #[test]
    fn unallocated_page_is_an_error() {
        let mut s = MemPageStore::new();
        let mut buf = [0u8; PAGE_SIZE];
        assert!(s.read_page(0, &mut buf).is_err());
        assert!(s.write_page(0, &buf).is_err());
    }

    #[test]
    fn trait_is_object_safe() {
        let mut s: Box<dyn PageStore> = Box::new(MemPageStore::new());
        let id = s.allocate_page().unwrap();
        assert_eq!(id, 0);
    }
}
