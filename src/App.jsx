import { useCallback, useEffect, useMemo, useState } from "react";
import { api, AuthError, getToken, KIND_LABELS, setToken, timeAgo } from "./api.js";

export default function App() {
  const [token, setTok] = useState(getToken());
  const logout = useCallback(() => {
    setToken("");
    setTok("");
  }, []);
  if (!token) return <Login onLogin={(t) => (setToken(t), setTok(t))} />;
  return <Dashboard onLogout={logout} />;
}

/* ------------------------------------------------------------------ login */

function Login({ onLogin }) {
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/me", { token: pw });
      onLogin(pw);
    } catch (err) {
      setError(err instanceof AuthError ? "Wrong password." : err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <form className="card login-card" onSubmit={submit}>
        <Logo />
        <p className="muted">Get an email the minute a matching job is posted.</p>
        <label className="field">
          <span>Password</span>
          <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoFocus />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="btn primary" disabled={!pw || busy}>
          {busy ? "Checking…" : "Sign in"}
        </button>
        <p className="hint">This is the ADMIN_PASSWORD you set in Vercel.</p>
      </form>
    </div>
  );
}

function Logo() {
  return (
    <div className="logo">
      <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden="true">
        <rect width="32" height="32" rx="8" fill="var(--accent)" />
        <circle cx="15" cy="15" r="7" fill="none" stroke="#fff" strokeWidth="3" />
        <path d="M20 20l5 5" stroke="#fff" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <span>Job Notifier</span>
    </div>
  );
}

/* ------------------------------------------------------------------ dashboard */

function Dashboard({ onLogout }) {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("jobs");
  const [toast, setToast] = useState(null);
  const [running, setRunning] = useState(false);
  const [loadError, setLoadError] = useState("");

  const notify = useCallback((text, kind = "ok") => {
    setToast({ text, kind, id: Date.now() });
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  const load = useCallback(async () => {
    try {
      setData(await api("/api/overview"));
      setLoadError("");
    } catch (err) {
      if (err instanceof AuthError) onLogout();
      else setLoadError(err.message);
    }
  }, [onLogout]);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  async function runNow() {
    setRunning(true);
    try {
      const r = await api("/api/run", { method: "POST" });
      const parts = [`Checked ${r.checked} sources`, `${r.matched} new matches`];
      if (r.blocked) parts.push(`${r.blocked} skipped (clearance/citizenship/no visa)`);
      if (r.emailed) parts.push(`${r.emailed} emailed`);
      if (r.sheet_rows) parts.push(`${r.sheet_rows} added to sheet`);
      if (r.discovered) parts.push(`${r.discovered} companies discovered`);
      if (r.errors.length) parts.push(`${r.errors.length} errors`);
      notify(parts.join(" · "), r.errors.length ? "warn" : "ok");
      await load();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setRunning(false);
    }
  }

  if (!data) {
    return (
      <div className="center">
        {loadError ? (
          <div className="card narrow">
            <h3>Can't reach the backend</h3>
            <p className="error">{loadError}</p>
            <p className="muted">Check the environment variables in Vercel (DATABASE_URL especially), then reload.</p>
            <button className="btn" onClick={onLogout}>Sign out</button>
          </div>
        ) : (
          <div className="spinner" aria-label="Loading" />
        )}
      </div>
    );
  }

  const { sources, stats, setup, last_run: lastRun } = data;

  return (
    <div className="app">
      <header className="topbar">
        <Logo />
        <StatusPill setup={setup} />
        <div className="spacer" />
        {setup.sheet_url && (
          <a className="btn" href={setup.sheet_url} target="_blank" rel="noreferrer">Open sheet ↗</a>
        )}
        <button className="btn primary" onClick={runNow} disabled={running}>
          {running ? "Checking…" : "Check now"}
        </button>
        <button className="btn ghost" onClick={onLogout}>Sign out</button>
      </header>

      <main className="content">
        <SetupChecklist setup={setup} onGo={setTab} />

        <section className="stats">
          <Stat label="Matches · 24h" value={stats.matched_24h} />
          <Stat label="Emailed · 24h" value={stats.emailed_24h} />
          <Stat
            label="Career pages watched"
            value={(stats.auto_active + stats.manual_sources).toLocaleString()}
            hint={`${stats.auto_hiring.toLocaleString()} hiring US engineers now`}
          />
          <Stat label="Discovered · 24h" value={stats.auto_24h.toLocaleString()} hint={`${stats.queue_waiting} names waiting for lookup`} />
          <Stat
            label="Skipped · 24h"
            value={stats.blocked_24h + stats.unverified_24h}
            hint={`${stats.blocked_24h} not eligible · ${stats.unverified_24h} unverified`}
          />
          <AiStat ai={data.ai} setup={setup} />
          <Stat
            label="CPU · 24h"
            value={`${(stats.cpu_ms_24h / 1000).toFixed(0)}s`}
            hint={`${stats.runs_24h} runs · ~${((stats.cpu_ms_24h / 1000) * 30 / 3600).toFixed(1)}h/month`}
          />
        </section>

        <nav className="tabs" role="tablist">
          {[
            ["jobs", "Jobs"],
            ["sources", "Sources"],
            ["settings", "Settings"],
          ].map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "tab active" : "tab"} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
          <div className="spacer" />
          {lastRun && (
            <span className="muted small">
              Last check {timeAgo(lastRun.finished_at)} · {lastRun.checked} sources
            </span>
          )}
        </nav>

        {tab === "jobs" && <JobsTab refreshKey={lastRun?.id} />}
        {tab === "sources" && <SourcesTab sources={sources} stats={stats} seed={data.seed} reload={load} notify={notify} />}
        {tab === "settings" && <SettingsTab settings={data.settings} setup={setup} ai={data.ai} reload={load} notify={notify} />}
      </main>

      {toast && (
        <div key={toast.id} className={`toast ${toast.kind}`} role="status">
          {toast.text}
        </div>
      )}
    </div>
  );
}

function StatusPill({ setup }) {
  const last = setup.last_cron_run;
  const minutes = last ? (Date.now() - new Date(last.finished_at).getTime()) / 60000 : Infinity;
  let cls = "ok",
    text = `Watching · cron ${timeAgo(last?.finished_at)}`;
  if (!last) (cls = "warn"), (text = "Cron not set up yet");
  else if (minutes > 15) (cls = "bad"), (text = `Cron stalled · ${timeAgo(last.finished_at)}`);
  return <span className={`pill ${cls}`}><i />{text}</span>;
}

const usd = (n) => `$${Number(n || 0).toFixed(Number(n) < 1 ? 4 : 2)}`;

function AiStat({ ai, setup }) {
  if (!setup.ai_key) return <Stat label="AI check" value="Off" hint="Add ANTHROPIC_API_KEY to turn on" />;
  if (ai.remaining_usd != null) {
    return <Stat label="AI credit left (est.)" value={usd(ai.remaining_usd)} hint={`${usd(ai.month_usd)} this month · ${ai.month_calls} checks`} />;
  }
  return <Stat label="AI spend · month" value={usd(ai.month_usd)} hint={`${ai.month_calls} checks · enter your credit in Settings`} />;
}

function Stat({ label, value, hint }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

function SetupChecklist({ setup, onGo }) {
  const items = [
    [setup.database, "Database connected"],
    [setup.smtp, "Email (SMTP) configured", "Set SMTP_USER and SMTP_PASSWORD in Vercel"],
    [setup.cron_secret, "CRON_SECRET set", "Set CRON_SECRET in Vercel"],
    [!!setup.last_cron_run, "Every-minute pinger running", "Create the cron-job.org job (see Settings)"],
    [setup.sheets, "Google Sheet connected", "Set SHEETS_WEBHOOK_URL and SHEETS_SECRET in Vercel (see SETUP.md)"],
  ];
  if (items.every(([ok]) => ok)) return null;
  return (
    <section className="card setup">
      <h3>Finish setup</h3>
      <ul>
        {items.map(([ok, label, todo]) => (
          <li key={label} className={ok ? "done" : ""}>
            <span className="check">{ok ? "✓" : "○"}</span>
            {label}
            {!ok && todo && <span className="muted"> — {todo}</span>}
          </li>
        ))}
      </ul>
      <button className="btn small" onClick={() => onGo("settings")}>Open settings</button>
    </section>
  );
}

/* ------------------------------------------------------------------ jobs */

function JobsTab({ refreshKey }) {
  const [view, setView] = useState("matched");
  const [jobs, setJobs] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api(`/api/jobs?view=${view}&limit=300`)
      .then((j) => alive && (setJobs(j), setError("")))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [view, refreshKey]);

  return (
    <section className="card flush">
      <div className="card-head">
        <div className="segmented">
          {[
            ["matched", "Matches"],
            ["hidden", "Hidden only"],
            ["review", "Skipped (not eligible)"],
            ["new", "All new postings"],
            ["all", "Everything tracked"],
          ].map(([id, label]) => (
            <button key={id} className={view === id ? "active" : ""} onClick={() => setView(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error pad">{error}</p>}
      {!jobs && !error && <div className="pad muted">Loading…</div>}
      {jobs && jobs.length === 0 && (
        <div className="empty">
          <p>No jobs here yet.</p>
          <p className="muted">
            {view === "hidden"
              ? "Jobs appear here when a hidden company (Sources → Hidden companies) posts a new matching role. The roles already open when a company is found aren't sent, so the first ones show up as these companies post again."
              : "New postings that match your filters will appear here (and in your inbox) as soon as they're spotted."}
          </p>
        </div>
      )}
      {jobs && jobs.length > 0 && (
        <ul className="job-list">
          {jobs.map((j) => (
            <li key={j.id} className="job">
              <div className="job-main">
                <ChannelBadge channel={j.channel} />
                <TagBadges tags={j.tags} />
                <a href={j.url} target="_blank" rel="noreferrer" className="job-title">{j.title}</a>
                <div className="job-meta">
                  <strong>{j.company}</strong>
                  {j.location && <span> · {j.location}</span>}
                </div>
                {j.note && <div className={`job-note visa-${j.visa || "none"}`}>{j.note}</div>}
              </div>
              <div className="job-side">
                <VisaBadge visa={j.visa} />
                <span className={`badge kind-${j.source_kind}`}>{KIND_LABELS[j.source_kind] || j.source_kind}</span>
                {j.notified ? (
                  <span className="badge good">Emailed</span>
                ) : j.matched ? (
                  <span className="badge warn">Pending</span>
                ) : j.baseline ? (
                  <span className="badge dim">Already open</span>
                ) : (
                  <span className="badge dim">Filtered</span>
                )}
                <span className="muted small" title={new Date(j.first_seen_at).toLocaleString()}>{timeAgo(j.first_seen_at)}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const VISA = {
  stated: ["good", "Sponsors visas"],
  history: ["good", "H-1B sponsor"],
  listed: ["good", "Sponsor (your list)"],
  unknown: ["dim", "Sponsorship not mentioned"],
  blocked: ["bad", "Not eligible"],
  pending: ["dim", "Reading the posting…"],
};

const CHANNELS = {
  hidden_new: ["Hidden · first opening", "Found on a company's own career page; the company had no US engineering openings until now"],
  hidden: ["Hidden", "Found on a discovered company's own career page, usually before LinkedIn"],
  watchlist: ["Watchlist", "A company you added"],
  simplify: ["Simplify", "From the SimplifyJobs new grad list on GitHub (posted today)"],
  linkedin: ["LinkedIn", "Found through LinkedIn search"],
  jobboard: ["Job board", "Hacker News, YC, RemoteOK, We Work Remotely or Himalayas"],
};

function ChannelBadge({ channel }) {
  if (!CHANNELS[channel]) return null;
  const [label, title] = CHANNELS[channel];
  return <span className={`channel ch-${channel.replace("_new", "")}`} title={title}>{label}</span>;
}

const TAGS = {
  easy_apply: ["Easy Apply", "Applied on LinkedIn (Easy Apply), not on the company's own site"],
  recruiter: ["Recruiter / job site", "Posted by a staffing agency or job site, not the hiring company (AI check)"],
  unchecked: ["⚠ Not checked", "LinkedIn wouldn't show this posting for hours, so it wasn't checked for citizenship, clearance or years of experience: open it to verify"],
};

function TagBadges({ tags }) {
  return (tags || "")
    .split(",")
    .filter((t) => TAGS[t])
    .map((t) => (
      <span key={t} className="channel ch-tag" title={TAGS[t][1]}>{TAGS[t][0]}</span>
    ));
}

function VisaBadge({ visa }) {
  if (!VISA[visa]) return null;
  const [cls, label] = VISA[visa];
  return <span className={`badge ${cls}`}>{label}</span>;
}

function H1bLine({ record }) {
  if (!record) return <p className="small muted">No H-1B approvals found under this name in USCIS data (FY2021–23).</p>;
  return (
    <p className="small visa-history">
      ✓ {record.total} H-1B approvals ({record.initial} new) as {record.legal_name}
    </p>
  );
}

/* ------------------------------------------------------------------ sources */

function SourcesTab({ sources, stats, seed, reload, notify }) {
  const groups = useMemo(
    () => ({
      manual: sources.filter((s) => s.origin === "manual"),
      preset: sources.filter((s) => s.origin === "preset"),
    }),
    [sources]
  );

  return (
    <>
      <DiscoveryStatus stats={stats} seed={seed} />
      <AddSource reload={reload} notify={notify} />
      <SourceGroup
        title="Your companies"
        subtitle="Checked every 2 minutes."
        rows={groups.manual}
        empty="Add companies you care about above. Paste a careers URL or type a name."
        reload={reload}
        notify={notify}
      />
      <SourceGroup
        title="Discovery feeds"
        subtitle="LinkedIn and job streams. Every company seen hiring engineers here gets its own career page looked up and watched directly."
        rows={groups.preset}
        reload={reload}
        notify={notify}
      />
      <AutoSources reload={reload} notify={notify} />
    </>
  );
}

const SEED_HOSTS = ["Greenhouse", "Greenhouse", "Ashby", "Lever", "SmartRecruiters", "Workday"];

function DiscoveryStatus({ stats, seed }) {
  let seedText = "Starting soon";
  if (seed?.done_at) seedText = `Done (${(seed.added || 0).toLocaleString()} career pages added; refreshes monthly)`;
  else if (seed?.crawl) {
    seedText = `Reading ${SEED_HOSTS[seed.host] || "job boards"} (${seed.host + 1} of 6), part ${seed.page + 1}` +
      (seed.pages ? ` of ${seed.pages}` : "") + ` · ${(seed.added || 0).toLocaleString()} career pages added`;
  }
  return (
    <section className="card">
      <h3>Finding companies you don't know</h3>
      <div className="discovery-grid">
        <div><strong>{(stats.auto_active + stats.manual_sources).toLocaleString()}</strong><span>career pages watched</span></div>
        <div>
          <strong>{stats.auto_hiring.toLocaleString()}</strong>
          <span>hiring US engineers{stats.auto_cycle_min ? ` (each checked about every ${stats.auto_cycle_min} min)` : ""}</span>
        </div>
        <div><strong>{stats.auto_unchecked.toLocaleString()}</strong><span>waiting for their first check</span></div>
        <div><strong>{stats.queue_waiting.toLocaleString()}</strong><span>names from LinkedIn/feeds to look up</span></div>
      </div>
      <p className="small muted">
        Web index (Common Crawl): {seedText}
        {seed?.last_error && <span className="error"> · last try failed ({seed.last_error}), retrying automatically</span>}
      </p>
      <p className="small muted">
        GitHub scanner:{" "}
        {stats.scanner_last_at ? (
          <>
            last run <strong>{timeAgo(stats.scanner_last_at)}</strong> ·{" "}
            {Number(stats.scanner_checks_24h).toLocaleString()} career page checks in 24h
          </>
        ) : (
          <>not running yet (add the DATABASE_URL secret on GitHub; see SETUP.md)</>
        )}
      </p>
    </section>
  );
}

function AutoSources({ reload, notify }) {
  const [q, setQ] = useState("");
  const [data, setData] = useState({ total: 0, rows: [] });
  const [limit, setLimit] = useState(25);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setData(await api(`/api/sources/auto?q=${encodeURIComponent(q)}&limit=${limit}`));
      setError("");
    } catch (err) {
      setError(err.message);
    }
  }, [q, limit]);

  useEffect(() => {
    const t = setTimeout(load, 250);
    return () => clearTimeout(t);
  }, [load]);

  const reloadAll = () => (load(), reload());

  return (
    <section className="card flush">
      <div className="card-head">
        <div>
          <h3>Hidden companies <span className="count">{data.total.toLocaleString()}</span></h3>
          <p className="muted small">
            Found automatically and watched directly on their own career pages; their new jobs are labeled Hidden.
            Companies with US engineering openings are listed first and checked every 15 minutes; the rest every few hours.
          </p>
        </div>
        <input className="search" placeholder="Search companies…" value={q} onChange={(e) => (setQ(e.target.value), setLimit(25))} />
      </div>
      {error && <p className="pad error">{error}</p>}
      {!error && data.total === 0 && <p className="pad muted">{q ? "No matches." : "Nothing discovered yet. This fills up after the first few checks."}</p>}
      <ul className="source-list">
        {data.rows.map((s) => <SourceRow key={s.id} s={s} reload={reloadAll} notify={notify} />)}
      </ul>
      {data.rows.length < data.total && (
        <button className="link-btn pad" onClick={() => setLimit((l) => l + 50)}>
          Show more ({(data.total - data.rows.length).toLocaleString()} more)
        </button>
      )}
    </section>
  );
}

function AddSource({ reload, notify }) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState(null);
  const [error, setError] = useState("");
  const [showLinkedIn, setShowLinkedIn] = useState(false);

  const looksLikeUrl = /\.[a-z]{2,}(\/|$)|^https?:\/\//i.test(q.trim()) && !/\s/.test(q.trim());

  async function find(e) {
    e.preventDefault();
    if (!q.trim()) return;
    setBusy(true);
    setError("");
    setResults(null);
    try {
      if (looksLikeUrl) {
        setResults([await api("/api/detect", { method: "POST", body: { url: q.trim() } })]);
      } else {
        const hits = await api(`/api/discover?q=${encodeURIComponent(q.trim())}`);
        if (!hits.length) setError(`Couldn't find "${q.trim()}" on Greenhouse, Lever, Ashby, Workable, SmartRecruiters or Recruitee. Paste their careers page URL instead.`);
        setResults(hits);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function add(r, name) {
    try {
      await api("/api/sources", { method: "POST", body: { name, kind: r.kind, config: r.config } });
      notify(`Watching ${name}. You'll get an email when it posts a matching job.`);
      setResults(null);
      setQ("");
      reload();
    } catch (err) {
      notify(err.message, "error");
    }
  }

  return (
    <section className="card">
      <h3>Watch a company</h3>
      <form className="row" onSubmit={find}>
        <input
          className="grow"
          placeholder="Company name (e.g. Ramp) or careers page URL"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="btn primary" disabled={busy || !q.trim()}>
          {busy ? "Searching…" : looksLikeUrl ? "Detect" : "Find"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {results?.map((r) => <DetectedCard key={r.kind + JSON.stringify(r.config)} r={r} onAdd={add} />)}
      <button className="link-btn" onClick={() => setShowLinkedIn((v) => !v)}>
        {showLinkedIn ? "− Hide LinkedIn search" : "+ Add a LinkedIn search"}
      </button>
      {showLinkedIn && <LinkedInForm onAdd={add} />}
    </section>
  );
}

function DetectedCard({ r, onAdd }) {
  const [name, setName] = useState(r.name);
  return (
    <div className="detected">
      <div className="row">
        <span className={`badge kind-${r.kind}`}>{KIND_LABELS[r.kind]}</span>
        <input className="grow" value={name} onChange={(e) => setName(e.target.value)} aria-label="Name" />
        <button className="btn primary" onClick={() => onAdd(r, name)} disabled={!name.trim()}>Watch</button>
      </div>
      {r.note && <p className="muted small">{r.note}</p>}
      {r.kind !== "linkedin" && r.kind !== "generic" && <H1bLine record={r.h1b} />}
      <p className="small">
        <strong>{r.job_count}</strong> open jobs right now{" "}
        {r.url && <a href={r.url} target="_blank" rel="noreferrer">view board ↗</a>}
      </p>
      {r.sample.length > 0 && (
        <ul className="sample">
          {r.sample.map((s, i) => (
            <li key={i}>{s.title}{s.location && <span className="muted"> · {s.location}</span>}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LinkedInForm({ onAdd }) {
  const [keywords, setKeywords] = useState("");
  const [location, setLocation] = useState("United States");
  const [remote, setRemote] = useState(false);
  const name = `LinkedIn: ${[keywords, location].filter(Boolean).join(" · ")}${remote ? " · remote" : ""}`;
  return (
    <div className="detected">
      <div className="grid-3">
        <label className="field"><span>Keywords</span><input value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder="e.g. LLM engineer" /></label>
        <label className="field"><span>Location</span><input value={location} onChange={(e) => setLocation(e.target.value)} /></label>
        <label className="check-field"><input type="checkbox" checked={remote} onChange={(e) => setRemote(e.target.checked)} /> Remote only</label>
      </div>
      <p className="muted small">
        Best-effort: LinkedIn limits automated access, so searches are checked every 10 minutes. Company boards are faster and more reliable.
      </p>
      <button className="btn primary" disabled={!keywords.trim()} onClick={() => onAdd({ kind: "linkedin", config: { keywords, location, remote } }, name)}>
        Add search
      </button>
    </div>
  );
}

function SourceGroup({ title, subtitle, rows, empty, searchable, collapsedLimit, reload, notify }) {
  const [filter, setFilter] = useState("");
  const [showAll, setShowAll] = useState(false);
  const filtered = filter ? rows.filter((s) => s.name.toLowerCase().includes(filter.toLowerCase())) : rows;
  const visible = collapsedLimit && !showAll && !filter ? filtered.slice(0, collapsedLimit) : filtered;

  return (
    <section className="card flush">
      <div className="card-head">
        <div>
          <h3>{title} <span className="count">{rows.length}</span></h3>
          {subtitle && <p className="muted small">{subtitle}</p>}
        </div>
        {searchable && rows.length > 0 && (
          <input className="search" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        )}
      </div>
      {rows.length === 0 && empty && <p className="pad muted">{empty}</p>}
      <ul className="source-list">
        {visible.map((s) => <SourceRow key={s.id} s={s} reload={reload} notify={notify} />)}
      </ul>
      {visible.length < filtered.length && (
        <button className="link-btn pad" onClick={() => setShowAll(true)}>Show all {filtered.length}</button>
      )}
    </section>
  );
}

function SourceRow({ s, reload, notify }) {
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState(false);
  const status = !s.enabled ? "off" : s.last_error ? "bad" : s.initialized ? "ok" : "warn";

  async function run(fn) {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const toggle = () => run(async () => {
    await api(`/api/sources/${s.id}`, { method: "PATCH", body: { enabled: !s.enabled } });
    reload();
  });
  const remove = () => {
    if (!confirm(`Stop watching ${s.name}?`)) return;
    run(async () => {
      await api(`/api/sources/${s.id}`, { method: "DELETE" });
      reload();
    });
  };
  const runTest = () => run(async () => setTest(await api(`/api/sources/${s.id}/test`, { method: "POST" })));
  const setInterval_ = (v) => run(async () => {
    await api(`/api/sources/${s.id}`, { method: "PATCH", body: { interval_minutes: Number(v) } });
    reload();
  });

  return (
    <li className="source">
      <div className="source-main">
        <span className={`dot ${status}`} title={status} />
        <div className="grow">
          <div className="source-name">
            {s.url ? <a href={s.url} target="_blank" rel="noreferrer">{s.name}</a> : s.name}
            <span className={`badge kind-${s.kind}`}>{KIND_LABELS[s.kind] || s.kind}</span>
          </div>
          <div className="muted small">
            {s.enabled ? `checked ${timeAgo(s.last_checked_at)}` : "paused"}
            {s.last_job_count != null && ` · ${s.last_job_count} open`}
            {s.origin === "auto" && s.relevant_count != null && ` · ${s.relevant_count} US engineering`}
            {` · ${s.match_count} matches`}
          </div>
          {s.last_error && <div className="error small">{s.last_error}</div>}
        </div>
        <select value={s.interval_minutes} onChange={(e) => setInterval_(e.target.value)} disabled={busy} aria-label="Check interval">
          {[...new Set([1, 2, 5, 10, 15, 30, 60, 180, 360, s.interval_minutes])].sort((a, b) => a - b).map((m) => (
            <option key={m} value={m}>every {m < 60 ? `${m}m` : `${m / 60}h`}</option>
          ))}
        </select>
        <div className="actions">
          <button className="btn small" onClick={runTest} disabled={busy}>Test</button>
          <button className="btn small" onClick={toggle} disabled={busy}>{s.enabled ? "Pause" : "Resume"}</button>
          <button className="btn small danger" onClick={remove} disabled={busy} aria-label={`Delete ${s.name}`}>✕</button>
        </div>
      </div>
      {test && (
        <div className="test-result">
          <div className="row">
            <strong>
              {test.job_count} jobs found · {test.match_count} match your role/location filters
              {test.checked > 0 && ` · first ${test.checked} checked for visa/clearance`}
            </strong>
            <div className="spacer" />
            <button className="link-btn" onClick={() => setTest(null)}>close</button>
          </div>
          <ul className="sample">
            {test.sample.map((j, i) => (
              <li key={i} className={j.matches ? "" : "muted"}>
                {j.matches ? "✓ " : "· "}
                <a href={j.url} target="_blank" rel="noreferrer">{j.title}</a>
                {j.location && <span className="muted"> · {j.location}</span>}{" "}
                <VisaBadge visa={j.visa} />
                {(j.note || j.filter) && <div className="small muted test-note">{j.note || j.filter}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/* ------------------------------------------------------------------ settings */

function SheetCard({ setup, reload, notify }) {
  const [busy, setBusy] = useState(false);
  async function test() {
    setBusy(true);
    try {
      const r = await api("/api/test-sheet", { method: "POST" });
      notify(`Connected to your sheet (${r.rows} jobs in the “${r.sheet}” tab).`);
      reload();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <div className="row wrap">
        <div className="grow">
          <h3>Google Sheet</h3>
          <p className="muted small">
            {setup.sheets
              ? "Every job you're emailed is also added to your sheet (newest first), with Status and Notes columns for tracking applications."
              : "Not connected. Follow the Google Sheet steps in SETUP.md, then add SHEETS_WEBHOOK_URL and SHEETS_SECRET in Vercel and redeploy."}
          </p>
        </div>
        {setup.sheet_url && <a className="btn" href={setup.sheet_url} target="_blank" rel="noreferrer">Open sheet ↗</a>}
        <button className="btn" onClick={test} disabled={busy || !setup.sheets}>{busy ? "Testing…" : "Test connection"}</button>
      </div>
    </section>
  );
}

const toText = (list) => (list || []).join(", ");
const toList = (text) => text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);

function AiCard({ ai, setup, reload, notify }) {
  const [busy, setBusy] = useState(false);
  async function test() {
    setBusy(true);
    try {
      const r = await api("/api/test-ai", { method: "POST" });
      const f = r.facts;
      notify(`AI check works (${usd(r.cost_usd)}). Sample posting → citizenship required: ${f.citizenship_required ? "yes" : "no"}, clearance: ${f.clearance_required ? "yes" : "no"}, min years: ${f.min_years_required}.`);
      reload();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <div className="row wrap">
        <div className="grow">
          <h3>AI check (Claude Haiku)</h3>
          <p className="muted small">
            {setup.ai_key
              ? "Every job that passes the rules is also read by Claude Haiku, which catches citizenship, clearance, sponsorship and years-of-experience requirements the rules miss. About $0.0002–0.001 per job."
              : "Off: add ANTHROPIC_API_KEY in Vercel and redeploy (SETUP.md, step 9). Until then jobs are checked by the rules only."}
          </p>
        </div>
        <button className="btn" onClick={test} disabled={busy || !setup.ai_key}>{busy ? "Testing…" : "Test AI check"}</button>
      </div>
      {setup.ai_key && (
        <div className="discovery-grid">
          <div><strong>{usd(ai.today_usd)}</strong><span>spent today</span></div>
          <div><strong>{usd(ai.month_usd)}</strong><span>this month ({ai.month_calls} checks)</span></div>
          <div><strong>{ai.remaining_usd != null ? usd(ai.remaining_usd) : "—"}</strong><span>credit left (estimate)</span></div>
          <div><strong>{ai.monthly_limit_usd ? usd(ai.monthly_limit_usd) : "none"}</strong><span>monthly limit</span></div>
        </div>
      )}
      {ai.today_errors > 0 && ai.last_error && <p className="small error">Last AI error: {ai.last_error}</p>}
      <p className="small muted">
        "Credit left" is your entered credit minus what this app has spent since you entered it. Anthropic doesn't offer an
        API for the exact balance; check <a href="https://console.anthropic.com/settings/billing" target="_blank" rel="noreferrer">Console → Billing</a>.
      </p>
    </section>
  );
}

function SettingsTab({ settings, setup, ai, reload, notify }) {
  const [form, setForm] = useState(() => ({
    ...settings,
    include_keywords: toText(settings.include_keywords),
    exclude_keywords: toText(settings.exclude_keywords),
    exclude_companies: toText(settings.exclude_companies),
    location_keywords: toText(settings.location_keywords),
    sponsor_companies: toText(settings.sponsor_companies),
    seniority_exclude: toText(settings.seniority_exclude),
  }));
  const [saving, setSaving] = useState(false);
  const [sending, setSending] = useState(false);
  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          ...form,
          include_keywords: toList(form.include_keywords),
          exclude_keywords: toList(form.exclude_keywords),
          exclude_companies: toList(form.exclude_companies),
          location_keywords: toList(form.location_keywords),
          sponsor_companies: toList(form.sponsor_companies),
          seniority_exclude: toList(form.seniority_exclude),
          max_years_experience: Number(form.max_years_experience) || 0,
          max_age_minutes: Number(form.max_age_minutes) || 60,
          linkedin_max_age_minutes: Number(form.linkedin_max_age_minutes) || 1440,
          auto_interval_minutes: Number(form.auto_interval_minutes) || 15,
          auto_idle_interval_minutes: Number(form.auto_idle_interval_minutes) || 1440,
          ai_monthly_limit_usd: Number(form.ai_monthly_limit_usd) || 0,
          ai_credit_usd: Number(form.ai_credit_usd) || 0,
        },
      });
      notify("Settings saved. New filters apply to jobs found from now on.");
      reload();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setSaving(false);
    }
  }

  async function testEmail() {
    setSending(true);
    try {
      const r = await api("/api/test-email", { method: "POST" });
      const cc = r.cc && r.cc.length ? ` (CC: ${r.cc.join(", ")})` : "";
      notify(`Test email sent to ${r.to}${cc}. Check your inbox (and spam).`);
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setSending(false);
    }
  }

  const cronUrl = `${window.location.origin}/api/cron?key=YOUR_CRON_SECRET`;

  return (
    <>
      <AiCard ai={ai} setup={setup} reload={reload} notify={notify} />
      <SheetCard setup={setup} reload={reload} notify={notify} />
      <form className="card settings" onSubmit={save}>
        <h3>Notifications</h3>
        <div className="row wrap">
          <label className="field grow">
            <span>Send emails to</span>
            <input type="email" value={form.notify_email} onChange={set("notify_email")} placeholder={setup.notify_to || "you@example.com"} />
          </label>
          <button type="button" className="btn" onClick={testEmail} disabled={sending || !setup.smtp}>
            {sending ? "Sending…" : "Send test email"}
          </button>
        </div>
        <label className="field">
          <span>CC: also send every email to (separate several addresses with commas)</span>
          <input type="text" value={form.cc_emails || ""} onChange={set("cc_emails")} placeholder="friend@example.com" />
        </label>
        <label className="check-field"><input type="checkbox" checked={form.paused} onChange={set("paused")} /> Pause emails (jobs are still tracked)</label>

        <h3>What to look for</h3>
        <label className="field">
          <span>Job title must contain one of (comma separated)</span>
          <textarea rows={4} value={form.include_keywords} onChange={set("include_keywords")} />
        </label>
        <label className="field">
          <span>…and none of</span>
          <textarea rows={2} value={form.exclude_keywords} onChange={set("exclude_keywords")} />
        </label>
        <label className="field">
          <span>Never email jobs from these companies (defense contractors are pre-filled; add staffing agencies here)</span>
          <input value={form.exclude_companies} onChange={set("exclude_companies")} placeholder="e.g. Jobot, Dice" />
        </label>
        <div className="row wrap">
          <label className="check-field"><input type="checkbox" checked={form.us_only} onChange={set("us_only")} /> US jobs only (plus remote jobs open to the US)</label>
        </div>
        <div className="grid-3">
          <label className="field">
            <span>Also allow these locations</span>
            <input value={form.location_keywords} onChange={set("location_keywords")} placeholder="e.g. Toronto" />
          </label>
          <label className="field">
            <span>Only email jobs posted within the last (minutes)</span>
            <input type="number" min="5" max="1440" value={form.max_age_minutes} onChange={set("max_age_minutes")} />
          </label>
          <label className="field">
            <span>LinkedIn jobs: posted within the last (minutes; LinkedIn often lists jobs an hour or more late)</span>
            <input type="number" min="5" max="1440" value={form.linkedin_max_age_minutes} onChange={set("linkedin_max_age_minutes")} />
          </label>
        </div>

        <h3>Experience level</h3>
        <label className="field">
          <span>Skip job titles containing (levels like III, IV, L5+, E5+ and “Engineer 3” are skipped too; “Member of Technical Staff” is allowed)</span>
          <textarea rows={2} value={form.seniority_exclude} onChange={set("seniority_exclude")} />
        </label>
        <label className="field narrow-field">
          <span>Skip jobs whose description asks for more than … years of experience (0 = don't check)</span>
          <input type="number" min="0" max="30" value={form.max_years_experience} onChange={set("max_years_experience")} />
        </label>

        <h3>Work authorization</h3>
        <p className="muted small">
          For each new matching job the full description is read before emailing. Company sponsorship history comes from
          USCIS H-1B approval data (FY2021–23).
        </p>
        <label className="check-field">
          <input type="checkbox" checked={form.skip_ineligible} onChange={set("skip_ineligible")} />
          Skip jobs that need a security clearance, US citizenship, a green card, or "US persons" (ITAR), or that say no visa sponsorship
        </label>
        <fieldset className="radio-group">
          <legend>Visa sponsorship</legend>
          {[
            ["allow_unknown", "Everything without restrictions (recommended)", "Email every job that doesn't require clearance, citizenship or a green card and doesn't say “no sponsorship”. Jobs that don't mention sponsorship are included and labeled; known sponsors are listed first."],
            ["require", "Only confirmed sponsors", "Email only if the posting mentions sponsorship or the company has H-1B approvals. Stricter: skips young startups with no H-1B history yet (you can review them under “Skipped”)."],
            ["off", "Don't check sponsorship", "Still skips clearance/citizenship jobs if the box above is on."],
          ].map(([value, label, help]) => (
            <label key={value} className="radio">
              <input type="radio" name="visa_mode" value={value} checked={form.visa_mode === value} onChange={set("visa_mode")} />
              <span><strong>{label}</strong><span className="muted small"> — {help}</span></span>
            </label>
          ))}
        </fieldset>
        <label className="field">
          <span>Companies you know sponsor (treated as sponsors even without H-1B records)</span>
          <input value={form.sponsor_companies} onChange={set("sponsor_companies")} placeholder="e.g. LiveKit, Mastra" />
        </label>

        <h3>AI check</h3>
        <label className="check-field">
          <input type="checkbox" checked={form.ai_check} onChange={set("ai_check")} /> Double-check jobs with Claude Haiku before emailing (needs ANTHROPIC_API_KEY)
        </label>
        <div className="grid-3">
          <label className="field">
            <span>Credit you loaded in the Anthropic Console ($)</span>
            <input type="number" min="0" step="0.01" value={form.ai_credit_usd} onChange={set("ai_credit_usd")} placeholder="e.g. 5" />
          </label>
          <label className="field">
            <span>Monthly spending limit ($, 0 = no limit)</span>
            <input type="number" min="0" step="0.5" value={form.ai_monthly_limit_usd} onChange={set("ai_monthly_limit_usd")} />
          </label>
        </div>

        <h3>Discovery</h3>
        <label className="check-field"><input type="checkbox" checked={form.auto_discover} onChange={set("auto_discover")} /> Automatically find and watch company career pages (from LinkedIn, the feeds and the Common Crawl web index)</label>
        <div className="grid-3">
          <label className="field">
            <span>Companies with 10+ US engineering openings: check every (minutes; 3–9 openings: 2×, 1–2: 4×)</span>
            <input type="number" min="5" max="1440" value={form.auto_interval_minutes} onChange={set("auto_interval_minutes")} />
          </label>
          <label className="field">
            <span>Other companies: check every (minutes)</span>
            <input type="number" min="30" max="10080" value={form.auto_idle_interval_minutes} onChange={set("auto_idle_interval_minutes")} />
          </label>
        </div>

        <div className="row">
          <button className="btn primary" disabled={saving}>{saving ? "Saving…" : "Save settings"}</button>
        </div>
      </form>

      <section className="card">
        <h3>Every-minute checks (free)</h3>
        <p className="muted small">
          Vercel's free plan only allows a daily cron, so a free service pings the checker every minute instead.
        </p>
        <ol className="steps">
          <li>Create a free account at <a href="https://cron-job.org" target="_blank" rel="noreferrer">cron-job.org</a>.</li>
          <li>Create a cron job with this URL (replace the key with your CRON_SECRET):<code className="block">{cronUrl}</code></li>
          <li>Schedule: <strong>every 1 minute</strong>. Save and enable it.</li>
        </ol>
        <p className="small">
          Status: {setup.last_cron_run ? <>last cron run <strong>{timeAgo(setup.last_cron_run.finished_at)}</strong></> : <strong>no cron runs yet</strong>}
        </p>
      </section>
    </>
  );
}
