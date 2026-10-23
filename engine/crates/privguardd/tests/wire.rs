//! The pgwire server driven by a minimal hand-written client over a real socket.

mod common;
use common::*;
use privguard_core::json::{self, Json};
use privguardd::engine::Engine;
use privguardd::server::serve;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

const SERVICE_SECRET: &str = "engine-service-secret-for-tests";

async fn start() -> std::net::SocketAddr {
    let db = database();
    let engine = Arc::new(Mutex::new(Engine::open(&db, GOLDEN_SECRET).unwrap()));
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(serve(
        listener,
        engine,
        Arc::new(SERVICE_SECRET.to_string()),
    ));
    addr
}

struct Client(TcpStream);

type Msg = (u8, Vec<u8>);

impl Client {
    async fn connect(addr: std::net::SocketAddr) -> Client {
        Client(TcpStream::connect(addr).await.unwrap())
    }

    async fn send_startup(&mut self, user: &str) {
        let mut body = 196_608i32.to_be_bytes().to_vec();
        for (k, v) in [("user", user), ("database", "safehealth")] {
            body.extend_from_slice(k.as_bytes());
            body.push(0);
            body.extend_from_slice(v.as_bytes());
            body.push(0);
        }
        body.push(0);
        let mut packet = ((body.len() + 4) as i32).to_be_bytes().to_vec();
        packet.extend(body);
        self.0.write_all(&packet).await.unwrap();
    }

    async fn send(&mut self, tag: u8, body: &[u8]) {
        let mut m = vec![tag];
        m.extend_from_slice(&((body.len() + 4) as i32).to_be_bytes());
        m.extend_from_slice(body);
        self.0.write_all(&m).await.unwrap();
    }

    async fn recv(&mut self) -> Option<Msg> {
        let tag = self.0.read_u8().await.ok()?;
        let len = self.0.read_i32().await.ok()? as usize;
        let mut body = vec![0u8; len - 4];
        self.0.read_exact(&mut body).await.ok()?;
        Some((tag, body))
    }

    /// Messages up to and including the next ReadyForQuery.
    async fn until_ready(&mut self) -> Vec<Msg> {
        let mut out = Vec::new();
        while let Some(m) = self.recv().await {
            let done = m.0 == b'Z';
            out.push(m);
            if done {
                break;
            }
        }
        out
    }

    async fn login(addr: std::net::SocketAddr, user: &str) -> Client {
        let mut c = Client::connect(addr).await;
        c.send_startup(user).await;
        let (tag, body) = c.recv().await.unwrap();
        assert_eq!(
            (tag, body),
            (b'R', vec![0, 0, 0, 3]),
            "cleartext password requested"
        );
        c.send(b'p', format!("{SERVICE_SECRET}\0").as_bytes()).await;
        let hello = c.until_ready().await;
        assert_eq!(hello[0], (b'R', vec![0, 0, 0, 0]));
        assert!(hello.iter().any(|m| m.0 == b'S'), "parameter status sent");
        c
    }

    async fn query(&mut self, sql: &str) -> Vec<Msg> {
        self.send(b'Q', format!("{sql}\0").as_bytes()).await;
        self.until_ready().await
    }
}

fn tags(msgs: &[Msg]) -> Vec<u8> {
    msgs.iter().map(|m| m.0).collect()
}

fn text(msg: &Msg) -> String {
    String::from_utf8_lossy(&msg.1).to_string()
}

/// Cells of a `DataRow` as text (`None` = NULL).
fn cells(msg: &Msg) -> Vec<Option<String>> {
    let n = i16::from_be_bytes([msg.1[0], msg.1[1]]) as usize;
    let mut pos = 2;
    let mut out = Vec::new();
    for _ in 0..n {
        let len = i32::from_be_bytes(msg.1[pos..pos + 4].try_into().unwrap());
        pos += 4;
        if len < 0 {
            out.push(None);
        } else {
            out.push(Some(
                String::from_utf8_lossy(&msg.1[pos..pos + len as usize]).to_string(),
            ));
            pos += len as usize;
        }
    }
    out
}

fn notice_json(msgs: &[Msg]) -> Json {
    let n = msgs.iter().find(|m| m.0 == b'N').expect("a notice");
    // Fields are `M<message>\0`; the JSON is the M field.
    let body = text(n);
    let start = body.find('M').unwrap() + 1;
    let end = body[start..].find('\0').unwrap() + start;
    json::parse(&body[start..end]).unwrap()
}

#[tokio::test]
async fn a_client_can_log_in_run_a_private_count_and_see_it_charged() {
    let addr = start().await;
    let mut c = Client::connect(addr).await;
    // TLS is declined; the client carries on in the clear.
    c.0.write_all(&[0, 0, 0, 8, 4, 210, 22, 47]).await.unwrap();
    assert_eq!(c.0.read_u8().await.unwrap(), b'N');
    drop(c);

    let mut c = Client::login(addr, "alice").await;
    assert_eq!(
        tags(&c.query("SET privguard.rho = 0.5").await),
        [b'C', b'Z']
    );

    let r = c.query("SELECT COUNT(*) FROM visits").await;
    assert_eq!(tags(&r), [b'T', b'D', b'N', b'C', b'Z']);
    // The oracle's committed answer for count_all (same seed, rho 0.5).
    let want = expected("count_all")
        .get("rows")
        .unwrap()
        .as_array()
        .unwrap()[0]
        .get("value")
        .unwrap()
        .as_i64()
        .unwrap();
    assert_eq!(cells(&r[1]), [Some(want.to_string())]);
    assert!(text(&r[3]).starts_with("SELECT 1"));
    let n = notice_json(&r);
    assert_eq!(n.get("status").unwrap().as_str(), Some("executed"));
    assert_eq!(
        n.get("mechanism").unwrap().as_str(),
        Some("discrete_laplace")
    );
    assert_eq!(n.get("rho_charged").unwrap().as_f64(), Some(0.5));
    let spent = n
        .get("budget")
        .unwrap()
        .get("rho_spent")
        .unwrap()
        .as_f64()
        .unwrap();
    assert!((spent - 0.5).abs() < 1e-12);

    // A repeat is served from the noise cache at zero cost.
    let again = c.query("SELECT COUNT(*) FROM visits").await;
    assert_eq!(cells(&again[1]), cells(&r[1]));
    let n = notice_json(&again);
    assert_eq!(n.get("status").unwrap().as_str(), Some("cache_hit"));
    assert_eq!(n.get("rho_charged").unwrap().as_f64(), Some(0.0));

    // SHOW budget reflects the single charge.
    let b = c.query("SHOW budget").await;
    let row = cells(&b[1]);
    assert_eq!(row[1].as_deref(), Some("alice"));
    assert_eq!(row[3].as_deref(), Some("0.5"));
    assert_eq!(row[6].as_deref(), Some("active"));

    let l = c.query("SHOW ledger").await;
    assert_eq!(cells(&l[1])[0].as_deref(), Some("t"));
    assert_eq!(cells(&l[1])[1].as_deref(), Some("2"));

    let q = c.query("SHOW queries").await;
    let statuses: Vec<_> = q
        .iter()
        .filter(|m| m.0 == b'D')
        .map(|m| cells(m)[2].clone().unwrap())
        .collect();
    assert_eq!(statuses, ["executed", "cache_hit"]);

    c.send(b'X', &[]).await;
}

#[tokio::test]
async fn grouped_results_return_one_row_per_surviving_group() {
    let addr = start().await;
    let mut c = Client::login(addr, "alice").await;
    c.query("SET privguard.rho = 0.5").await;
    let r = c
        .query("SELECT admission_type, COUNT(*) FROM visits GROUP BY admission_type")
        .await;
    let rows: Vec<_> = r.iter().filter(|m| m.0 == b'D').map(cells).collect();
    assert!(!rows.is_empty());
    assert!(rows.iter().all(|r| r.len() == 2));
    assert_eq!(rows[0][0].as_deref(), Some("ELECTIVE"));
    // The oracle fixture for this exact query (same seed) says how many groups were suppressed.
    let want = expected("count_by_admission")
        .get("suppressed_groups")
        .unwrap()
        .as_i64()
        .unwrap();
    assert_eq!(
        notice_json(&r)
            .get("suppressed_groups")
            .unwrap()
            .as_i64()
            .unwrap(),
        want
    );
}

#[tokio::test]
async fn explain_returns_the_plan_as_json_and_spends_nothing() {
    let addr = start().await;
    let mut c = Client::login(addr, "alice").await;
    let r = c
        .query("EXPLAIN SELECT SUM(total_charge) FROM visits WHERE dept_id = 1")
        .await;
    assert_eq!(tags(&r), [b'T', b'D', b'C', b'Z']);
    let plan = json::parse(&cells(&r[1])[0].clone().unwrap()).unwrap();
    assert_eq!(plan.get("physical").unwrap().as_array().unwrap().len(), 5);
    let b = c.query("SHOW budget").await;
    assert_eq!(cells(&b[1])[3].as_deref(), Some("0"));
}

#[tokio::test]
async fn errors_carry_sqlstates_and_the_connection_survives_them() {
    let addr = start().await;
    let mut c = Client::login(addr, "frank").await;

    let r = c.query("DROP TABLE visits").await;
    assert_eq!(tags(&r), [b'E', b'Z']);
    assert!(text(&r[0]).contains("C42601"), "{}", text(&r[0]));

    let r = c
        .query("SELECT COUNT(*) FROM visits WHERE patient_id = 1")
        .await;
    assert!(text(&r[0]).contains("C42501"));

    // frank holds 0.2: the second query exceeds it.
    c.query("SET privguard.rho = 0.15").await;
    assert!(tags(&c.query("SELECT COUNT(*) FROM visits").await).contains(&b'C'));
    let r = c.query("SELECT SUM(total_charge) FROM visits").await;
    assert_eq!(tags(&r), [b'E', b'Z']);
    assert!(text(&r[0]).contains("C53400"), "{}", text(&r[0]));

    // Still usable afterwards, and an empty query is answered politely.
    assert_eq!(
        tags(&c.query("SHOW budget").await),
        [b'T', b'D', b'C', b'Z']
    );
    assert_eq!(tags(&c.query("  ;  ").await), [b'I', b'Z']);
    assert_eq!(tags(&c.query("BEGIN").await), [b'C', b'Z']);
}

#[tokio::test]
async fn the_extended_protocol_is_refused_but_does_not_kill_the_session() {
    let addr = start().await;
    let mut c = Client::login(addr, "alice").await;
    c.send(b'P', b"stmt\0SELECT 1\0\0\0").await;
    let r = c.until_ready().await;
    assert_eq!(tags(&r), [b'E', b'Z']);
    assert!(text(&r[0]).contains("C0A000"));
    assert_eq!(
        tags(&c.query("SHOW budget").await),
        [b'T', b'D', b'C', b'Z']
    );
}

#[tokio::test]
async fn a_wrong_service_password_is_refused_with_a_fatal_error() {
    let addr = start().await;
    let mut c = Client::connect(addr).await;
    c.send_startup("alice").await;
    assert_eq!(c.recv().await.unwrap().0, b'R');
    c.send(b'p', b"not-the-secret\0").await;
    let (tag, body) = c.recv().await.unwrap();
    assert_eq!(tag, b'E');
    let body = String::from_utf8_lossy(&body);
    assert!(body.contains("C28P01") && body.contains("FATAL"));
    assert!(c.recv().await.is_none(), "the server closed the connection");
}

#[tokio::test]
async fn an_unknown_principal_is_refused_even_with_the_right_secret() {
    let addr = start().await;
    let mut c = Client::connect(addr).await;
    c.send_startup("mallory").await;
    c.recv().await.unwrap();
    c.send(b'p', format!("{SERVICE_SECRET}\0").as_bytes()).await;
    let (tag, body) = c.recv().await.unwrap();
    assert_eq!(tag, b'E');
    assert!(String::from_utf8_lossy(&body).contains("C28000"));
}

#[tokio::test]
async fn oversized_and_malformed_packets_close_the_connection() {
    let addr = start().await;
    let mut c = Client::connect(addr).await;
    // Claims a 2 GiB startup packet.
    c.0.write_all(&i32::MAX.to_be_bytes()).await.unwrap();
    assert!(c.recv().await.is_none());

    let mut c = Client::login(addr, "alice").await;
    c.0.write_all(&[b'Q', 0x7F, 0xFF, 0xFF, 0xFF])
        .await
        .unwrap();
    assert!(c.recv().await.is_none());
}

#[tokio::test]
async fn principals_see_only_their_own_audit_rows() {
    let addr = start().await;
    let mut alice = Client::login(addr, "alice").await;
    alice.query("SELECT COUNT(*) FROM visits").await;
    let mut erin = Client::login(addr, "erin").await;
    erin.query("SELECT COUNT(*) FROM visits").await;
    let mine = alice.query("SHOW queries").await;
    assert_eq!(mine.iter().filter(|m| m.0 == b'D').count(), 1);
    let all = erin.query("SHOW queries").await;
    assert_eq!(all.iter().filter(|m| m.0 == b'D').count(), 2);
    let budgets = erin.query("SHOW budgets").await;
    assert_eq!(budgets.iter().filter(|m| m.0 == b'D').count(), 3);
}

#[tokio::test]
async fn the_policy_grid_statements_work_over_the_wire_for_a_dba_only() {
    let addr = start().await;
    let mut dave = Client::login(addr, "dave").await;

    let p = dave.query("SHOW policies").await;
    let rows: Vec<_> = p.iter().filter(|m| m.0 == b'D').map(cells).collect();
    assert_eq!(rows.len(), 8);
    let charge = rows
        .iter()
        .find(|r| r[1].as_deref() == Some("total_charge"))
        .unwrap();
    assert_eq!(charge[3].as_deref(), Some("aggregate_only"));
    assert_eq!(charge[5].as_deref(), Some("0"));
    assert_eq!(charge[6].as_deref(), Some("1000"));
    assert_eq!(charge[7].as_deref(), Some("t"));

    let roles = dave.query("SHOW roles").await;
    assert_eq!(roles.iter().filter(|m| m.0 == b'D').count(), 4);

    assert_eq!(
        tags(
            &dave
                .query("GRANT SELECT (admit_ts) ON visits TO clinician")
                .await
        ),
        [b'C', b'Z']
    );
    let g = dave.query("SHOW grants").await;
    let granted: Vec<_> = g.iter().filter(|m| m.0 == b'D').map(cells).collect();
    assert!(granted
        .iter()
        .any(|r| r[0].as_deref() == Some("clinician") && r[2].as_deref() == Some("admit_ts")));
    assert_eq!(
        tags(
            &dave
                .query("REVOKE SELECT (admit_ts) ON visits FROM clinician")
                .await
        ),
        [b'C', b'Z']
    );

    for bad in [
        "GRANT ALL ON visits TO clinician",
        "GRANT SELECT (dept_id) ON visits TO clinician; DROP TABLE x",
        "GRANT SELECT (dept id) ON visits TO clinician",
    ] {
        let r = dave.query(bad).await;
        assert!(text(&r[0]).contains("C42601"), "{bad}: {}", text(&r[0]));
    }

    let mut alice = Client::login(addr, "alice").await;
    for stmt in [
        "SHOW policies",
        "GRANT SELECT (dept_id) ON visits TO clinician",
    ] {
        let r = alice.query(stmt).await;
        assert!(text(&r[0]).contains("C42501"), "{stmt}: {}", text(&r[0]));
    }
}
