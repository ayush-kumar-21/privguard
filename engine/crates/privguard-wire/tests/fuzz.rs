//! Fuzz-style tests for the pgwire frame parsers (Phase 4): arbitrary bytes from an untrusted
//! client must yield `Ok` or `Err`, never a panic.

use privguard_wire::codec::{parse_frontend, parse_startup, split_backend};
use proptest::prelude::*;

proptest! {
    #[test]
    fn startup_packets_never_panic(body in prop::collection::vec(any::<u8>(), 0..400)) {
        let _ = parse_startup(&body);
    }

    #[test]
    fn frontend_messages_never_panic(tag in any::<u8>(), body in prop::collection::vec(any::<u8>(), 0..400)) {
        let _ = parse_frontend(tag, &body);
    }

    /// The common messages with a corrupted length/terminator, which is where parsers slip.
    #[test]
    fn mutated_real_messages_never_panic(
        tag in prop_oneof![Just(b'Q'), Just(b'p'), Just(b'X')],
        sql in "[ -~]{0,60}",
        cut in 0usize..70,
        flip in any::<u8>(),
    ) {
        let mut body = sql.into_bytes();
        body.push(0);
        body.truncate(cut.min(body.len()));
        if let Some(b) = body.first_mut() {
            *b ^= flip;
        }
        let _ = parse_frontend(tag, &body);
    }

    #[test]
    fn backend_framing_never_panics(buf in prop::collection::vec(any::<u8>(), 0..64)) {
        let _ = split_backend(&buf);
    }
}
