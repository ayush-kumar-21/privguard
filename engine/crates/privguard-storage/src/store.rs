//! The page I/O seam. Phase 0 freeze.
//!
//! The buffer pool and B+ tree talk to storage only through [`PageStore`], so they
//! can be unit-tested over [`MemPageStore`] and later run over the real file-backed
//! store without changes.

use privguard_core::{Error, Result, PAGE_SIZE};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::Mutex;

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

/// `PageStore` over one file on disk: page `n` lives at byte offset `n * PAGE_SIZE`.
///
/// Reads take `&self` (the trait requires it), so the file handle sits behind a mutex.
#[derive(Debug)]
pub struct FilePageStore {
    file: Mutex<File>,
    pages: u32,
}

impl FilePageStore {
    fn io(e: std::io::Error) -> Error {
        Error::engine(format!("page file i/o: {e}"))
    }

    /// Open the file at `path`, creating it empty if it does not exist.
    pub fn open(path: &Path) -> Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(Self::io)?;
        let len = file.metadata().map_err(Self::io)?.len();
        if len % PAGE_SIZE as u64 != 0 {
            return Err(Error::engine(format!(
                "{} is {len} bytes, not a whole number of pages",
                path.display()
            )));
        }
        let pages = u32::try_from(len / PAGE_SIZE as u64)
            .map_err(|_| Error::engine("page file too large"))?;
        Ok(FilePageStore {
            file: Mutex::new(file),
            pages,
        })
    }

    /// Create the file at `path`, discarding any previous content.
    pub fn create(path: &Path) -> Result<Self> {
        File::create(path).map_err(Self::io)?;
        Self::open(path)
    }

    fn check(&self, id: PageId) -> Result<()> {
        if id < self.pages {
            Ok(())
        } else {
            Err(Error::engine(format!("page {id} out of range")))
        }
    }
}

impl PageStore for FilePageStore {
    fn read_page(&self, id: PageId, buf: &mut PageBuf) -> Result<()> {
        self.check(id)?;
        let mut f = self.file.lock().unwrap();
        f.seek(SeekFrom::Start(id as u64 * PAGE_SIZE as u64))
            .map_err(Self::io)?;
        f.read_exact(buf).map_err(Self::io)
    }

    fn write_page(&mut self, id: PageId, buf: &PageBuf) -> Result<()> {
        self.check(id)?;
        let f = self.file.get_mut().unwrap();
        f.seek(SeekFrom::Start(id as u64 * PAGE_SIZE as u64))
            .map_err(Self::io)?;
        f.write_all(buf).map_err(Self::io)
    }

    fn allocate_page(&mut self) -> Result<PageId> {
        let id = self.pages;
        let f = self.file.get_mut().unwrap();
        f.seek(SeekFrom::Start(id as u64 * PAGE_SIZE as u64))
            .map_err(Self::io)?;
        f.write_all(&[0u8; PAGE_SIZE]).map_err(Self::io)?;
        self.pages += 1;
        Ok(id)
    }

    fn page_count(&self) -> u32 {
        self.pages
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

#[cfg(test)]
mod file_store_tests {
    use super::*;
    use std::sync::atomic::{AtomicU32, Ordering};

    fn temp_path(tag: &str) -> std::path::PathBuf {
        static N: AtomicU32 = AtomicU32::new(0);
        std::env::temp_dir().join(format!(
            "privguard-{tag}-{}-{}.pages",
            std::process::id(),
            N.fetch_add(1, Ordering::Relaxed)
        ))
    }

    #[test]
    fn pages_survive_close_and_reopen() {
        let path = temp_path("file-store");
        let mut s = FilePageStore::create(&path).unwrap();
        let a = s.allocate_page().unwrap();
        let b = s.allocate_page().unwrap();
        let mut page = [0u8; PAGE_SIZE];
        page[0] = 1;
        page[PAGE_SIZE - 1] = 2;
        s.write_page(b, &page).unwrap();
        drop(s);

        let s = FilePageStore::open(&path).unwrap();
        assert_eq!(s.page_count(), 2);
        let mut out = [0u8; PAGE_SIZE];
        s.read_page(b, &mut out).unwrap();
        assert_eq!(out[..], page[..]);
        s.read_page(a, &mut out).unwrap();
        assert!(out.iter().all(|&x| x == 0));
        assert!(s.read_page(2, &mut out).is_err());
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn create_discards_old_content_and_open_rejects_torn_files() {
        let path = temp_path("file-store-torn");
        let mut s = FilePageStore::create(&path).unwrap();
        s.allocate_page().unwrap();
        drop(s);
        assert_eq!(FilePageStore::create(&path).unwrap().page_count(), 0);

        std::fs::write(&path, [0u8; 100]).unwrap();
        assert!(FilePageStore::open(&path).is_err());
        std::fs::remove_file(path).unwrap();
    }
}
