//! Page-based storage, built from scratch — no external database engine.
//!
//! Layout is frozen in `SPEC.md` §5: 8 KiB slotted pages addressed by RID, one
//! heap file per table, a CLOCK-eviction buffer pool in front of them, and a B+
//! tree for point and range lookup.
//!
//! Landed in Phase 2 (`IMPLEMENTATION_PLAN.md` §2.1–2.4).

pub mod heap;
pub mod page;
pub mod store;

pub use heap::HeapFile;
pub use page::{crc32c, page_checksum, SlottedPage};
pub use privguard_core::{Rid, PAGE_SIZE};
pub use store::{MemPageStore, PageBuf, PageId, PageStore};

/// Offsets and sizes of the page header, frozen in `SPEC.md` §5.1.
///
/// These live here rather than in the page module so the B+ tree and the heap
/// file agree on the header shape without depending on each other.
pub mod layout {
    /// `page_id u32 | page_kind u8 | flags u8 | slot_count u16 | free_start u16
    ///  | free_end u16 | lsn u64 | checksum u32`
    pub const PAGE_HEADER_SIZE: usize = 24;

    /// `offset u16 | length u16`
    pub const SLOT_SIZE: usize = 4;

    /// Largest tuple that can ever fit on an empty page.
    pub const MAX_TUPLE_SIZE: usize = super::PAGE_SIZE - PAGE_HEADER_SIZE - SLOT_SIZE;
}

/// What a page holds. Stored in the header's `page_kind` byte.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum PageKind {
    /// File header — row count and free-space map root. Always page 0.
    FileHeader = 0,
    /// Tuples, slot-addressed.
    Heap = 1,
    /// B+ tree internal node: separator keys and child page ids.
    BTreeInternal = 2,
    /// B+ tree leaf: `(key, RID)` pairs plus a right-sibling pointer.
    BTreeLeaf = 3,
}

// The page layout is frozen (`SPEC.md` §5.1), so these are compile-time facts
// rather than runtime tests: a `const` assertion fails the build instead of
// failing a test run, and unlike `assert!` in a `#[test]` it cannot be
// optimised away.
const _: () = assert!(layout::PAGE_HEADER_SIZE == 24);
const _: () = assert!(layout::SLOT_SIZE == 4);
const _: () = assert!(layout::MAX_TUPLE_SIZE == PAGE_SIZE - 24 - 4);
// A tuple must be able to use essentially the whole page, or the overhead
// arithmetic above is wrong.
const _: () = assert!(layout::MAX_TUPLE_SIZE > 8000);

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn page_kinds_are_distinct() {
        // The discriminants are written into the page header, so two kinds
        // sharing a byte would make a B+ tree leaf indistinguishable from a
        // heap page on read.
        let kinds = [
            PageKind::FileHeader,
            PageKind::Heap,
            PageKind::BTreeInternal,
            PageKind::BTreeLeaf,
        ];
        let mut tags: Vec<u8> = kinds.iter().map(|k| *k as u8).collect();
        tags.sort_unstable();
        let before = tags.len();
        tags.dedup();
        assert_eq!(tags.len(), before);
    }

    #[test]
    fn file_header_is_page_kind_zero() {
        // Page 0 of every heap file is the header, and a freshly zeroed page
        // must therefore read back as one rather than as a corrupt heap page.
        assert_eq!(PageKind::FileHeader as u8, 0);
    }
}
