//! 8 KiB slotted page with CRC32C checksum. `SPEC.md` §5.1 (`IMPLEMENTATION_PLAN.md` 2.1).
//!
//! Header layout (24 bytes, little-endian):
//! `page_id u32 | page_kind u8 | flags u8 | slot_count u16 | free_start u16 | free_end u16 | lsn u64 | checksum u32`
//!
//! Slot directory starts at byte 24 (`PAGE_HEADER_SIZE`) and grows downward (`free_start`
//! increases by 4 per slot). Tuple bytes grow upward from the end of the page (`free_end`
//! decreases). Deleting a tuple zeroes its 4-byte slot `(0, 0)` without compacting;
//! compaction runs only when an insert needs space that deleted tuples can provide, and
//! only updates live slot offsets (never slot indices, so RIDs remain stable).

use crate::layout::{MAX_TUPLE_SIZE, PAGE_HEADER_SIZE, SLOT_SIZE};
use crate::{PageBuf, PageId, PageKind};
use privguard_core::{Error, Result, Rid, PAGE_SIZE};

const CHECKSUM_OFFSET: usize = 20;

const CRC32C_TABLE: [u32; 256] = {
    let mut table = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut crc = i as u32;
        let mut bit = 0;
        while bit < 8 {
            if crc & 1 != 0 {
                crc = (crc >> 1) ^ 0x82F6_3B78;
            } else {
                crc >>= 1;
            }
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
};

/// Compute the Castagnoli CRC32C checksum over `data`.
pub fn crc32c(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for &b in data {
        let idx = ((crc as u8) ^ b) as usize;
        crc = (crc >> 8) ^ CRC32C_TABLE[idx];
    }
    !crc
}

/// Compute the page checksum, treating the checksum field (`20..24`) as zero.
pub fn page_checksum(buf: &PageBuf) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for (i, &b) in buf.iter().enumerate() {
        let byte = if (CHECKSUM_OFFSET..CHECKSUM_OFFSET + 4).contains(&i) {
            0
        } else {
            b
        };
        let idx = ((crc as u8) ^ byte) as usize;
        crc = (crc >> 8) ^ CRC32C_TABLE[idx];
    }
    !crc
}

impl PageKind {
    pub fn from_u8(v: u8) -> Result<Self> {
        match v {
            0 => Ok(PageKind::FileHeader),
            1 => Ok(PageKind::Heap),
            2 => Ok(PageKind::BTreeInternal),
            3 => Ok(PageKind::BTreeLeaf),
            other => Err(Error::engine(format!("invalid page_kind byte: {other}"))),
        }
    }
}

/// An 8 KiB slotted page (`SPEC.md` §5.1).
#[derive(Debug, Clone)]
pub struct SlottedPage {
    buf: PageBuf,
}

impl SlottedPage {
    /// Initialise a fresh, empty page.
    pub fn new(page_id: PageId, kind: PageKind) -> Self {
        let mut page = SlottedPage {
            buf: [0u8; PAGE_SIZE],
        };
        page.buf[0..4].copy_from_slice(&page_id.to_le_bytes());
        page.buf[4] = kind as u8;
        page.buf[5] = 0;
        page.set_slot_count(0);
        page.set_free_start(PAGE_HEADER_SIZE as u16);
        page.set_free_end(PAGE_SIZE as u16);
        page.set_lsn(0);
        page.seal_checksum();
        page
    }

    /// Decode and verify an 8 KiB page image from storage.
    pub fn from_bytes(buf: &PageBuf) -> Result<Self> {
        let stored_crc = u32::from_le_bytes(buf[20..24].try_into().unwrap());
        let expected_crc = page_checksum(buf);
        if stored_crc != expected_crc {
            return Err(Error::engine(format!(
                "page checksum mismatch: stored {stored_crc:#010x}, computed {expected_crc:#010x}"
            )));
        }
        let _ = PageKind::from_u8(buf[4])?;
        let page = SlottedPage { buf: *buf };
        let expected_free_start = PAGE_HEADER_SIZE + (page.slot_count() as usize) * SLOT_SIZE;
        if page.free_start() as usize != expected_free_start
            || (page.free_start() as usize) > (page.free_end() as usize)
            || (page.free_end() as usize) > PAGE_SIZE
        {
            return Err(Error::engine("corrupt page header pointers"));
        }
        Ok(page)
    }

    /// Seal the CRC32C checksum and return the 8 KiB page bytes.
    pub fn to_bytes(&self) -> PageBuf {
        let mut out = self.buf;
        let crc = page_checksum(&out);
        out[20..24].copy_from_slice(&crc.to_le_bytes());
        out
    }

    pub fn page_id(&self) -> PageId {
        u32::from_le_bytes(self.buf[0..4].try_into().unwrap())
    }

    pub fn kind(&self) -> PageKind {
        PageKind::from_u8(self.buf[4]).expect("validated page_kind")
    }

    pub fn flags(&self) -> u8 {
        self.buf[5]
    }

    pub fn set_flags(&mut self, flags: u8) {
        self.buf[5] = flags;
    }

    pub fn slot_count(&self) -> u16 {
        u16::from_le_bytes(self.buf[6..8].try_into().unwrap())
    }

    fn set_slot_count(&mut self, v: u16) {
        self.buf[6..8].copy_from_slice(&v.to_le_bytes());
    }

    pub fn free_start(&self) -> u16 {
        u16::from_le_bytes(self.buf[8..10].try_into().unwrap())
    }

    fn set_free_start(&mut self, v: u16) {
        self.buf[8..10].copy_from_slice(&v.to_le_bytes());
    }

    pub fn free_end(&self) -> u16 {
        u16::from_le_bytes(self.buf[10..12].try_into().unwrap())
    }

    fn set_free_end(&mut self, v: u16) {
        self.buf[10..12].copy_from_slice(&v.to_le_bytes());
    }

    pub fn lsn(&self) -> u64 {
        u64::from_le_bytes(self.buf[12..20].try_into().unwrap())
    }

    pub fn set_lsn(&mut self, lsn: u64) {
        self.buf[12..20].copy_from_slice(&lsn.to_le_bytes());
    }

    fn seal_checksum(&mut self) {
        let crc = page_checksum(&self.buf);
        self.buf[20..24].copy_from_slice(&crc.to_le_bytes());
    }

    fn slot_pos(slot: u16) -> usize {
        PAGE_HEADER_SIZE + (slot as usize) * SLOT_SIZE
    }

    fn read_slot(&self, slot: u16) -> (u16, u16) {
        let pos = Self::slot_pos(slot);
        let off = u16::from_le_bytes(self.buf[pos..pos + 2].try_into().unwrap());
        let len = u16::from_le_bytes(self.buf[pos + 2..pos + 4].try_into().unwrap());
        (off, len)
    }

    fn write_slot(&mut self, slot: u16, offset: u16, len: u16) {
        let pos = Self::slot_pos(slot);
        self.buf[pos..pos + 2].copy_from_slice(&offset.to_le_bytes());
        self.buf[pos + 2..pos + 4].copy_from_slice(&len.to_le_bytes());
    }

    /// Unallocated gap between the slot directory and the lowest tuple.
    pub fn contiguous_free_space(&self) -> usize {
        (self.free_end() as usize).saturating_sub(self.free_start() as usize)
    }

    /// Total free space if fragmented tuple space from deleted slots is reclaimed.
    pub fn total_free_space(&self) -> usize {
        let live_bytes: usize = (0..self.slot_count())
            .map(|s| {
                let (off, len) = self.read_slot(s);
                if off == 0 && len == 0 {
                    0
                } else {
                    len as usize
                }
            })
            .sum();
        PAGE_SIZE
            .saturating_sub(self.free_start() as usize)
            .saturating_sub(live_bytes)
    }

    /// Whether a tuple of `tuple_len` bytes can fit (after compaction if needed).
    pub fn can_fit(&self, tuple_len: usize) -> bool {
        if tuple_len > MAX_TUPLE_SIZE {
            return false;
        }
        let has_dead_slot = (0..self.slot_count()).any(|s| self.read_slot(s) == (0, 0));
        let needed = if has_dead_slot {
            tuple_len
        } else {
            tuple_len + SLOT_SIZE
        };
        self.total_free_space() >= needed
    }

    /// Compact live tuples to the end of the page, preserving slot indices.
    pub fn compact(&mut self) {
        let count = self.slot_count();
        let mut scratch = [0u8; PAGE_SIZE];
        let mut new_free_end = PAGE_SIZE;

        for slot in 0..count {
            let (off, len) = self.read_slot(slot);
            if off == 0 && len == 0 {
                continue;
            }
            let len_usize = len as usize;
            new_free_end -= len_usize;
            scratch[new_free_end..new_free_end + len_usize]
                .copy_from_slice(&self.buf[off as usize..off as usize + len_usize]);
            self.write_slot(slot, new_free_end as u16, len);
        }

        let free_start = self.free_start() as usize;
        self.buf[free_start..new_free_end].fill(0);
        self.buf[new_free_end..PAGE_SIZE].copy_from_slice(&scratch[new_free_end..PAGE_SIZE]);
        self.set_free_end(new_free_end as u16);
    }

    /// Insert a tuple and return its stable `Rid`.
    pub fn insert(&mut self, tuple: &[u8]) -> Result<Rid> {
        if tuple.len() > MAX_TUPLE_SIZE {
            return Err(Error::engine(format!(
                "tuple of {} bytes exceeds MAX_TUPLE_SIZE ({MAX_TUPLE_SIZE})",
                tuple.len()
            )));
        }
        let dead_slot = (0..self.slot_count()).find(|&s| self.read_slot(s) == (0, 0));
        let slot_overhead = if dead_slot.is_some() { 0 } else { SLOT_SIZE };
        let needed = tuple.len() + slot_overhead;

        if self.contiguous_free_space() < needed {
            if self.total_free_space() < needed {
                return Err(Error::engine("page has insufficient free space"));
            }
            self.compact();
        }

        let slot = match dead_slot {
            Some(s) => s,
            None => {
                let s = self.slot_count();
                self.set_slot_count(s + 1);
                self.set_free_start(self.free_start() + SLOT_SIZE as u16);
                s
            }
        };

        let new_free_end = (self.free_end() as usize) - tuple.len();
        self.buf[new_free_end..new_free_end + tuple.len()].copy_from_slice(tuple);
        self.set_free_end(new_free_end as u16);
        self.write_slot(slot, new_free_end as u16, tuple.len() as u16);

        Ok(Rid::new(self.page_id(), slot))
    }

    /// Read a tuple by slot number. Returns `Ok(None)` if the slot is deleted or out of range.
    pub fn get(&self, slot: u16) -> Result<Option<&[u8]>> {
        if slot >= self.slot_count() {
            return Ok(None);
        }
        let (off, len) = self.read_slot(slot);
        if off == 0 && len == 0 {
            return Ok(None);
        }
        let start = off as usize;
        let end = start + (len as usize);
        if start < (self.free_start() as usize) || end > PAGE_SIZE {
            return Err(Error::engine("corrupt slot bounds"));
        }
        Ok(Some(&self.buf[start..end]))
    }

    /// Mark a slot deleted `(0, 0)` without compacting (`SPEC.md` §5.1).
    pub fn delete(&mut self, slot: u16) -> Result<bool> {
        if slot >= self.slot_count() {
            return Ok(false);
        }
        let (off, len) = self.read_slot(slot);
        if off == 0 && len == 0 {
            return Ok(false);
        }
        self.write_slot(slot, 0, 0);
        Ok(true)
    }

    /// Iterate over all live `(Rid, tuple_bytes)` entries in ascending slot order.
    pub fn iter_live(&self) -> impl Iterator<Item = (Rid, &[u8])> + '_ {
        let pid = self.page_id();
        (0..self.slot_count()).filter_map(move |s| {
            let (off, len) = self.read_slot(s);
            if off == 0 && len == 0 {
                None
            } else {
                let start = off as usize;
                let end = start + (len as usize);
                Some((Rid::new(pid, s), &self.buf[start..end]))
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crc32c_matches_rfc3720_test_vector() {
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
    }

    #[test]
    fn rid_insert_read_delete_round_trip() {
        let mut page = SlottedPage::new(7, PageKind::Heap);
        let r0 = page.insert(b"alpha").unwrap();
        let r1 = page.insert(b"bravo-tuple").unwrap();
        let r2 = page.insert(b"charlie").unwrap();

        assert_eq!(r0, Rid::new(7, 0));
        assert_eq!(r1, Rid::new(7, 1));
        assert_eq!(r2, Rid::new(7, 2));

        let raw = page.to_bytes();
        let decoded = SlottedPage::from_bytes(&raw).unwrap();
        assert_eq!(decoded.page_id(), 7);
        assert_eq!(decoded.kind(), PageKind::Heap);
        assert_eq!(decoded.get(r0.slot).unwrap(), Some(&b"alpha"[..]));
        assert_eq!(decoded.get(r1.slot).unwrap(), Some(&b"bravo-tuple"[..]));
        assert_eq!(decoded.get(r2.slot).unwrap(), Some(&b"charlie"[..]));
    }

    #[test]
    fn delete_zeroes_slot_without_compacting_until_full() {
        let mut page = SlottedPage::new(1, PageKind::Heap);
        let r0 = page.insert(&[b'A'; 3000]).unwrap();
        let r1 = page.insert(&[b'B'; 3000]).unwrap();
        let free_end_before = page.free_end();

        assert!(page.delete(r0.slot).unwrap());
        assert_eq!(page.get(r0.slot).unwrap(), None);
        // Delete does not compact immediately.
        assert_eq!(page.free_end(), free_end_before);

        // Inserting a 3000-byte tuple triggers compaction and reuses dead slot 0,
        // while keeping r1's slot index (1) intact.
        let r_reused = page.insert(&[b'C'; 3000]).unwrap();
        assert_eq!(r_reused, r0);
        assert_eq!(page.get(r1.slot).unwrap(), Some(&[b'B'; 3000][..]));
        assert_eq!(page.get(r_reused.slot).unwrap(), Some(&[b'C'; 3000][..]));
    }

    #[test]
    fn crc_catches_a_flipped_byte() {
        let mut page = SlottedPage::new(3, PageKind::Heap);
        page.insert(b"sensitive patient visit").unwrap();
        let clean = page.to_bytes();

        for pos in [0usize, 7, PAGE_HEADER_SIZE, PAGE_SIZE - 5] {
            let mut corrupted = clean;
            corrupted[pos] ^= 0x01;
            assert!(
                SlottedPage::from_bytes(&corrupted).is_err(),
                "flipped bit at byte {pos} was not detected by CRC32C"
            );
        }
    }
}
