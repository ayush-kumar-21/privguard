//! Fuzz-style tests for the on-disk decoders (Phase 4): hostile bytes must produce an `Err`, never
//! a panic, and a single flipped byte in a valid page must never be accepted.

use privguard_core::{ColumnType, PAGE_SIZE};
use privguard_storage::page::SlottedPage;
use privguard_storage::tuple::decode_row;
use privguard_storage::{PageBuf, PageKind};
use proptest::prelude::*;

fn valid_page() -> PageBuf {
    let mut p = SlottedPage::new(3, PageKind::Heap);
    for i in 0..20u8 {
        p.insert(&vec![i; 10 + i as usize]).unwrap();
    }
    p.delete(4).unwrap();
    p.to_bytes()
}

/// Exercise every public operation on a page that decoded; none may panic.
fn poke(mut p: SlottedPage) {
    let _ = (p.slot_count(), p.free_start(), p.free_end(), p.lsn());
    let _ = (p.contiguous_free_space(), p.total_free_space());
    for s in [0u16, 1, 7, p.slot_count(), u16::MAX] {
        let _ = p.get(s);
    }
    let _ = p.iter_live().count();
    let _ = p.insert(b"hello");
    let _ = p.delete(0);
    p.compact();
    let _ = SlottedPage::from_bytes(&p.to_bytes());
}

proptest! {
    #[test]
    fn random_pages_never_panic(bytes in prop::collection::vec(any::<u8>(), PAGE_SIZE)) {
        let mut buf: PageBuf = [0; PAGE_SIZE];
        buf.copy_from_slice(&bytes);
        if let Ok(p) = SlottedPage::from_bytes(&buf) {
            poke(p);
        }
    }

    /// Mutating a valid page and fixing nothing up: the CRC must reject it.
    #[test]
    fn a_flipped_byte_in_a_valid_page_is_rejected(at in 0..PAGE_SIZE, mask in 1u8..=255) {
        let mut buf = valid_page();
        prop_assert!(SlottedPage::from_bytes(&buf).is_ok());
        buf[at] ^= mask;
        prop_assert!(SlottedPage::from_bytes(&buf).is_err(), "corruption at byte {at} accepted");
    }

    /// A page with a valid checksum but nonsense inside (corrupt-then-reseal) must not panic.
    #[test]
    fn resealed_garbage_headers_never_panic(
        header in prop::collection::vec(any::<u8>(), 32),
        tail in prop::collection::vec(any::<u8>(), 64),
    ) {
        let mut buf = valid_page();
        buf[..header.len()].copy_from_slice(&header);
        let n = buf.len();
        buf[n - tail.len()..].copy_from_slice(&tail);
        if let Ok(p) = SlottedPage::from_bytes(&buf) {
            poke(p);
        }
    }

    #[test]
    fn random_tuples_never_panic_the_row_decoder(
        bytes in prop::collection::vec(any::<u8>(), 0..300),
        types in prop::collection::vec(
            prop_oneof![
                Just(ColumnType::I32), Just(ColumnType::I64), Just(ColumnType::F64),
                Just(ColumnType::Text), Just(ColumnType::Timestamp), Just(ColumnType::Bool),
            ],
            0..10,
        ),
    ) {
        let _ = decode_row(&types, &bytes);
    }
}
