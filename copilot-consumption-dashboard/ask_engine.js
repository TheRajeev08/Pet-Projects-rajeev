/* Rule-based question answering over the dashboard model. No network, no LLM.
   Shared definitions (active_this_week, declining, ...) come from build_dashboard.py so the
   answers match ask.py / the Copilot skill. Usable in the browser and in Node (tests). */
function AskEngine(D) {
  "use strict";
  const A = D.accounts || [], P = D.portfolio || {};
  const isNum = v => typeof v === "number" && isFinite(v);
  const n0 = v => (isNum(v) ? v : 0);
  const HEALTH_RANK = { Green: 0, Yellow: 1, Red: 2 };

  // ---------------------------------------------------------------- metrics
  const METRICS = [
    { k: "mtd_gross", label: "month-to-date spend", re: /month[- ]to[- ]date|\bmtd\b/, f: "money" },
    { k: "gross_run_rate_month", label: "month run-rate spend", re: /run[- ]?rate|projected (spend|month)/, f: "money" },
    { k: "gross_28d", label: "UBB spend (28d)", re: /(28[- ]?d(ay)?s?|monthly|4[- ]weeks?|last month)\s*(ubb |gross )?(spend|usage|consumption)|(spend|usage|consumption)\s*(over|in|for)?\s*(the )?(last|past)?\s*(28 days|month|4 weeks)/, f: "money" },
    { k: "gross_90d", label: "UBB spend (90d)", re: /90[- ]?d(ay)?s?\s*(ubb |gross )?(spend|usage)|(spend|usage)\s*(over|in)?\s*(the )?(last|past)?\s*90 days|quarter(ly)? spend/, f: "money" },
    { k: "billable_7d", label: "billable spend (7d)", re: /billable|overage spend/, f: "money", wk: "billable" },
    { k: "aiu_7d", label: "AI units (7d)", re: /ai units?|\baius?\b|\btokens?\b/, wk: "aiu", wow: "wow_aiu" },
    { k: "gross_7d", label: "UBB spend (7d)", re: /\bspend(ing|s)?\b|\bubb\b|\bdollars?\b|\$|\bcosts?\b|\bconsumption\b|\busage\b|\bburn\b/, f: "money", wk: "gross", wow: "wow_gross" },
    { k: "active_28d", label: "28-day active users", re: /28[- ]?d(ay)?s?\s*active|monthly active|\bmau\b|active users? (over|in) (the )?(last |past )?(28 days|month)/, wk: "a28", wow: "wow_a28" },
    { k: "contracted_seats", label: "contracted Copilot seats", re: /contract(ed)? seats|licen[cs]ed seats|purchased seats|paid seats|contracted/ },
    { k: "utilization", label: "seat utilization", re: /utili[sz](ation|ed)|seat usage/, f: "pct" },
    { k: "at_risk", label: "users at risk", re: /at[- ]risk|risky users/, wk: "h_at_risk", wow: "wow_at_risk" },
    { k: "health", label: "health score", re: /health score|\bhealth\b/, f: "num2", wk: "h_health", wow: "wow_health" },
    { k: "acceptance_28d", label: "acceptance rate", re: /acceptance|accept(ed)? rate|suggestions? accepted/, f: "pct", wow: "wow_acceptance" },
    { k: "ghe_seats", label: "GHE seats", re: /ghe seats|github enterprise seats|github seats/ },
    { k: "total_arr", label: "GitHub ARR", re: /\barr\b|\brevenue\b|contract value/, f: "money" },
    { k: "assigned", label: "assigned seats", re: /assigned|\bseats?\b|\blicen[cs]es?\b/, wk: "h_assigned", wow: "wow_assigned" },
    { k: "trend_4w", label: "4-week trend", re: /\btrend\b|momentum/, f: "pctSigned" },
    { k: "active_7d", label: "weekly active users", re: /weekly active|\bwau\b|active users?|\busers?\b|developers?|\bdevs?\b|\bengineers?\b|adoption/, wk: "a7", wow: "wow_a7" },
  ];
  const MET = Object.fromEntries(METRICS.map(m => [m.k, m]));
  const weekVal = (a, key, w) => { const s = a[key]; return Array.isArray(s) && isNum(s[w]) ? s[w] : null; };
  function metricGetter(m, w) {
    if (w && m.wk) return a => weekVal(a, m.wk, w);
    if (m.k === "billable_7d" || m.k === "aiu_7d") return a => weekVal(a, m.wk, 0);
    return a => (isNum(a[m.k]) ? a[m.k] : null);
  }

  // ---------------------------------------------------------------- vocab
  const SURFACES = [
    { name: "Copilot CLI", re: /copilot cli|\bcli\b|command[- ]line|\bterminal\b/ },
    { name: "Code review", re: /code reviews?|pr reviews?|copilot review|reviewing prs/ },
    { name: "Coding agent", re: /coding agent|cloud agent|copilot agent|swe agent|autonomous agent/ },
    { name: "Copilot app", re: /copilot app|desktop app|\bdesktop\b/ },
    { name: "Third-party agents", re: /third[- ]party|claude code|\bcodex\b|opencode|\b3p\b/ },
    { name: "GitHub.com chat", re: /github\.com chat|web chat|chat on github|dotcom chat|github chat/ },
    { name: "IDE chat", re: /\bides?\b|vs ?code|jetbrains|visual studio|ide chat|editor chat/ },
    { name: "Mobile", re: /\bmobile\b|android|\bios\b/ },
    { name: "Security agents", re: /security agents?|code scanning|autofix/ },
    { name: "SDK / integrations", re: /\bsdk\b|slack integration/ },
  ];
  const MODEL_NAMES = Object.keys(P.models || {});
  const MODEL_FAMILIES = ["claude", "gpt", "gemini", "opus", "sonnet", "haiku", "grok", "codex"];
  const ATTRS = [];
  ["region", "territory", "segment", "industry"].forEach(field => {
    [...new Set(A.map(a => a[field]).filter(v => typeof v === "string" && v.length >= 4))].forEach(v => ATTRS.push({ field, value: v, key: norm(v) }));
  });
  const NAME_STOP = new Set(("the global solutions solution technologies technology india private limited ltd inc pvt software consulting services " +
    "systems group digital labs health active users user spend seats model models copilot github account accounts which what with this week last " +
    "risk green yellow red code review agent first coding cloud mobile desktop security terminal claude codex model better bright good edge field " +
    "financial focus future people safe marketplace matrix delivery double peak west delta pocket global " +
    "yellow").split(" "));
  const NAME_INDEX = A.map(a => {
    const nn = norm(a.name), toks = nn.split(" ").filter(Boolean);
    const keys = [nn];
    if (toks[0] && toks[0].length >= 4 && !NAME_STOP.has(toks[0])) keys.push(toks[0]);
    const joined = toks.join("");
    if (joined.length >= 5 && joined !== toks[0]) keys.push(joined);
    return { a, keys: [...new Set(keys)] };
  });

  function norm(s) { return String(s || "").toLowerCase().replace(/[’`]/g, "'").replace(/[^a-z0-9$%.'\- ]+/g, " ").replace(/\.(?=\s|$)/g, " ").replace(/\s+/g, " ").trim(); }
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const wordRe = w => new RegExp("(^|[^a-z0-9])" + esc(w) + "($|[^a-z0-9])");
  function negatedAt(q, idx) { return /(\bnot|n't|\bwithout|\bno|\bnever|\bnon)\s*(\S+\s+){0,2}$/.test(q.slice(Math.max(0, idx - 30), idx) + " "); }
  const label = (pl, sg) => ({ pl, sg: sg || pl });

  // ---------------------------------------------------------------- parse
  function parse(raw) {
    let q = norm(raw);
    const out = { q, filters: [], metric: null, metrics: [], rank: null, n: null, agg: null, intent: null, scope: "consuming", week: 0, accounts: [], notes: [] };
    if (!q) return out;
    if (/^(help|\?|examples?|what can (i|you) ask.*)$/.test(q)) { out.intent = "help"; return out; }
    const lastWeek = /\b(last|previous|prior|past) week\b/.test(q) && !/\bthis week\b/.test(q);

    // accounts by name (checked on the raw normalized text)
    const hits = NAME_INDEX.filter(x => x.keys.some(k => wordRe(k).test(q)));
    if (hits.length && hits.length <= 5) {
      out.accounts = hits.map(x => x.a);
      hits.forEach(x => x.keys.forEach(k => { q = q.replace(wordRe(k), " ACCT "); }));
    }

    // intents
    if (/what (changed|happened|moved)|what'?s (new|changed)|what is new|\bchanges\b|\balerts?\b|what should i (look|worry|focus)|anything (new|important|notable)|red flags|headlines?/.test(q)) out.intent = "changes";
    else if (/(which|what|top|popular|most used|breakdown|mix|split|by|per|compare)\s+(\S+\s+)?(surfaces?|features?|product areas?|integrations?|tools?)\b|surfaces? (mix|breakdown|adoption|usage)|where .* (spend|usage) (is )?(coming|going)/.test(q)) out.intent = "surfaces";
    else if (/(which|what|top|popular|most used|breakdown|mix|split|by|per|compare)\s+(\S+\s+)?(models?|llms?)\b|models? (mix|breakdown|usage|share)/.test(q)) out.intent = "models";

    // numeric comparisons (consume their text so "at least" etc. don't look like ranking)
    const cmpRe = /(more than|greater than|over|above|exceeding|at least|>=|>|less than|fewer than|under|below|at most|<=|<)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m|%)?\s*((?:(?!(?:and|or|with|where|that|who|in|using|renewing|but)\b)[a-z$%'\-]+\s*){0,4})/g;
    let m;
    const cmps = [];
    while ((m = cmpRe.exec(q))) cmps.push({ idx: m.index, len: m[0].length, op: m[1], v: parseFloat(m[2].replace(/,/g, "")) * (m[3] === "k" ? 1e3 : m[3] === "m" ? 1e6 : 1), pct: m[3] === "%", tail: m[4] || "", head: q.slice(Math.max(0, m.index - 40), m.index) });
    const betweenRe = /between\s*\$?(\d[\d,]*(?:\.\d+)?)\s*(k|%)?\s*and\s*\$?(\d[\d,]*(?:\.\d+)?)\s*(k|%)?\s*((?:(?!(?:and|or|with|where|that|who|in|using|renewing|but)\b)[a-z$%'\-]+\s*){0,4})/g;
    while ((m = betweenRe.exec(q))) {
      const mul = x => (x === "k" ? 1e3 : 1);
      cmps.push({ idx: m.index, len: m[0].length, op: "between", v: parseFloat(m[1].replace(/,/g, "")) * mul(m[2]), v2: parseFloat(m[3].replace(/,/g, "")) * mul(m[4] || m[2]), pct: m[2] === "%" || m[4] === "%", tail: m[5] || "", head: q.slice(Math.max(0, m.index - 40), m.index) });
    }
    let qn = q;
    cmps.sort((a, b) => b.idx - a.idx).forEach(c => { qn = qn.slice(0, c.idx) + " CMP " + qn.slice(c.idx + c.len); });

    // metrics mentioned (outside comparisons), ordered by specificity
    let qm = qn;
    for (const mt of METRICS) {
      const mm = qm.match(mt.re);
      if (mm) { out.metrics.push(mt); qm = qm.replace(mt.re, " MET "); }
    }
    // "active" words describing activity filters shouldn't become the active_7d metric
    out.metric = out.metrics[0] || null;
    const findMetric = s => METRICS.find(mt => mt.re.test(s)) || null;

    cmps.forEach(c => {
      const mt = findMetric(c.tail.split(/\b(and|or|with|where|that|who|in|using|renewing|but)\b/)[0]) || findMetric(c.tail) || findMetric(c.head.split(/\b(with|and|where|that|having|who)\b/).pop()) || out.metric || MET.active_7d;
      let v = c.v, v2 = c.v2;
      if (mt.f === "pct") { if (c.pct || v > 1) v /= 100; if (v2 != null && (c.pct || v2 > 1)) v2 /= 100; }
      const get = metricGetter(mt, lastWeek ? 1 : 0);
      const fv = x => (mt.f === "money" ? "$" + x.toLocaleString() : mt.f === "pct" ? Math.round(x * 100) + "%" : x.toLocaleString());
      let test, txt;
      if (c.op === "between") { test = a => isNum(get(a)) && get(a) >= v && get(a) <= v2; txt = `between ${fv(v)} and ${fv(v2)}`; }
      else if (/more|greater|over|above|exceed|^>$/.test(c.op)) { test = a => isNum(get(a)) && get(a) > v; txt = `more than ${fv(v)}`; }
      else if (/at least|>=/.test(c.op)) { test = a => isNum(get(a)) && get(a) >= v; txt = `at least ${fv(v)}`; }
      else if (/at most|<=/.test(c.op)) { test = a => n0(get(a)) <= v; txt = `at most ${fv(v)}`; }
      else { test = a => n0(get(a)) < v; txt = `less than ${fv(v)}`; }
      out.filters.push({ id: "cmp", l: label(`have ${txt} ${mt.label}`, `has ${txt} ${mt.label}`), test, cols: [mt.k] });
      if (!out.metrics.includes(mt)) out.metrics.push(mt);
      if (!out.metric) out.metric = mt;
    });

    // scope
    if (/\b(all|every) (owned |my )?accounts\b|\ball owned\b|including non[- ]consuming/.test(qn)) out.scope = "all";
    const notUsingCopilot = /(not|n't|never|no longer)\s+(been\s+)?(using|consuming|on|adopted|adopting)\s+copilot(?!\s+(cli|app|agents?|reviews?|chat|mobile|sdk|coding|code review))|\bnot consuming\b|\bnon[- ]consuming\b|whitespace|untapped|no copilot|without copilot|haven't adopted|not (yet )?adopted|never used/.test(qn);

    // activity
    const inactiveRe = /(\bnot|n't|\bno longer)\s+(been\s+|yet\s+|currently\s+)?active|\binactive\b|\bdormant\b|\bidle\b(?! seats)|no (activity|usage)|zero (usage|activity)|(haven't|hasn't|didn't|did not|have not|has not)\s+(used|been using)|not (being )?used/;
    if (notUsingCopilot && !/\b(this|last|previous|past) week\b|7 days/.test(qn)) out.scope = "not_consuming";
    else if (inactiveRe.test(qn) || (notUsingCopilot && /week|7 days/.test(qn))) {
      if (lastWeek) out.filters.push({ id: "inactive_last", l: label("were not active last week", "was not active last week"), test: a => a.consuming && !a.active_last_week, cols: ["active_7d", "gross_28d"] });
      else out.filters.push({ id: "inactive", l: label("are not active this week", "is not active this week"), test: a => a.inactive_this_week, cols: ["active_28d", "gross_28d", "health_cat"] });
      out.inactiveUsed = true;
    } else if (/stopped|went (quiet|silent|dark)|dropped off|\bchurn(ed|ing)?\b|became inactive|lost activity|fell off/.test(qn)) {
      out.filters.push({ id: "stopped", l: label("stopped this week (active last week, not this week)", "stopped this week (active last week, not this week)"), test: a => a.active_last_week && !a.active_this_week, cols: ["active_28d", "gross_28d"] });
    } else if (/\bnew(ly)? (active|consum\w*|users?|adopt\w*|accounts?|customers?)|started (using|consuming)|became active|re-?activated|came back|new this week/.test(qn)) {
      out.filters.push({ id: "started", l: label("became active this week", "became active this week"), test: a => a.active_this_week && !a.active_last_week, cols: ["active_7d", "gross_7d"] });
    } else if (/\bactive (this|last|previous) week\b|\b(are|is|were|was|currently|still) active\b|\bactive accounts\b|\bactive now\b/.test(qn)) {
      if (lastWeek) out.filters.push({ id: "active_last", l: label("were active last week", "was active last week"), test: a => a.active_last_week, cols: ["active_7d"] });
      else out.filters.push({ id: "active", l: label("are active this week", "is active this week"), test: a => a.active_this_week, cols: ["active_7d", "gross_7d"] });
    }
    if (/\bno (active )?users\b|zero (active )?users|without (active )?users/.test(qn)) out.filters.push({ id: "no_users", l: label("have no active users this week", "has no active users this week"), test: a => !(n0(a.active_7d) > 0), cols: ["active_7d", "gross_7d"] });
    if (/\bno (ubb )?(spend|usage[- ]based)|zero spend|without spend/.test(qn)) out.filters.push({ id: "no_spend", l: label("have no UBB spend this week", "has no UBB spend this week"), test: a => !(n0(a.gross_7d) > 0), cols: ["gross_7d", "active_7d"] });

    // health
    let hq = qn;
    const worse = /health\s+(\w+\s+)?(worsened|dropped|declined|fell|deteriorated|got worse|went down)|(worse|worsening|declining|deteriorating) health|(moved|went|turned|slipped) (to )?(red|yellow)/;
    const better = /health\s+(\w+\s+)?(improved|rose|went up|got better)|(improv\w+|better) health|(moved|went|turned) (to )?green/;
    if (worse.test(hq)) { out.filters.push({ id: "health_worse", l: label("had health worsen vs last week", "had health worsen vs last week"), test: a => a.health_moved && HEALTH_RANK[a.health_cat] > HEALTH_RANK[a.health_prev], cols: ["health_prev", "health_cat"] }); hq = hq.replace(worse, " "); }
    else if (better.test(hq)) { out.filters.push({ id: "health_better", l: label("had health improve vs last week", "had health improve vs last week"), test: a => a.health_moved && HEALTH_RANK[a.health_cat] < HEALTH_RANK[a.health_prev], cols: ["health_prev", "health_cat"] }); hq = hq.replace(better, " "); }
    else if (/health\s+(\w+\s+)?(changed|moved|shifted)|changed health|health (category )?change/.test(hq)) { out.filters.push({ id: "health_moved", l: label("changed health category vs last week", "changed health category vs last week"), test: a => a.health_moved, cols: ["health_prev", "health_cat"] }); hq = hq.replace(/health\s+(\w+\s+)?(changed|moved|shifted)|changed health|health (category )?change/, " "); }
    else {
      const cats = [];
      if (/\bred\b|\bunhealthy\b|poor health|bad health|critical health/.test(hq)) cats.push("Red");
      if (/\byellow\b|\bamber\b|at-risk health|medium health/.test(hq)) cats.push("Yellow");
      if (/\bgreen\b|(^|[^n])\bhealthy\b|good health/.test(hq.replace(/unhealthy/g, ""))) cats.push("Green");
      if (cats.length) out.filters.push({ id: "health_cat", l: label(`have ${cats.join(" or ")} health`, `has ${cats.join(" or ")} health`), test: a => cats.includes(a.health_cat), cols: ["health_cat", "health"] });
      else if (/no health( score)?|without (a )?health|missing health/.test(hq)) out.filters.push({ id: "health_none", l: label("have no health score", "has no health score"), test: a => !a.health_cat, cols: ["health_cat"] });
    }

    // ranking by change ("biggest drop in spend")
    const dropRe = /(biggest|largest|most|top|sharpest|greatest|steepest|major)\s+(\w+\s+)?(drops?|declines?|decreases?|falls?|loss(es)?|dips?|reductions?)|(dropped|declined|fell|decreased|shrank|lost) (the )?most/;
    const riseRe = /(biggest|largest|most|top|sharpest|greatest|major)\s+(\w+\s+)?(increases?|growth|gains?|jumps?|rises?|spikes?)|(grew|increased|rose|jumped|gained) (the )?most/;
    const downWords = /\b(declin\w*|decreas\w*|dropp\w*|drop|shrink\w*|falling|fell|going down|down\b|slowing|losing|reduc\w*)/;
    const upWords = /\b(grow\w*|increas\w*|rising|rose|expand\w*|gaining|up\b|accelerat\w*|jump\w*|spik\w*)/;
    const wowM = out.metric && out.metric.wow ? out.metric : MET.active_7d;
    if (dropRe.test(hq) || riseRe.test(hq)) {
      const dir = dropRe.test(hq) ? "down" : "up";
      out.rank = { key: wowM.wow, dir: dir === "down" ? "asc" : "desc", change: dir, metric: wowM };
      out.filters.push({ id: "wow_dir", l: label(`had ${wowM.label} ${dir === "down" ? "fall" : "rise"} week over week`, `had ${wowM.label} ${dir === "down" ? "fall" : "rise"} week over week`), test: a => (dir === "down" ? n0(a[wowM.wow]) < 0 : n0(a[wowM.wow]) > 0), cols: [wowM.k, wowM.wow] });
    } else {
      const d = downWords.test(hq) && !/drop(ped)? off/.test(hq), u = upWords.test(hq);
      if (d || u) {
        const dir = d ? "down" : "up";
        const generic = /(declin\w*|decreas\w*|dropp\w*|falling|grow\w*|increas\w*|rising|shrink\w*) (copilot )?(usage|adoption|activity|engagement)|(usage|adoption|activity|engagement) (is |are )?(declin|decreas|dropp|fall|grow|increas|ris|shrink)/.test(hq);
        if (out.metric && out.metric.wow && out.metric.k !== "active_7d" && !generic) {
          out.filters.push({ id: "wow_dir", l: label(`have ${out.metric.label} ${dir === "down" ? "down" : "up"} week over week`, `has ${out.metric.label} ${dir === "down" ? "down" : "up"} week over week`), test: a => (dir === "down" ? n0(a[out.metric.wow]) < 0 : n0(a[out.metric.wow]) > 0), cols: [out.metric.k, out.metric.wow] });
          out.defaultSort = { key: out.metric.wow, dir: dir === "down" ? "asc" : "desc" };
        } else {
          out.filters.push(dir === "down"
            ? { id: "declining", l: label("are declining (weekly actives down 10%+ over 4 weeks)", "is declining (weekly actives down 10%+ over 4 weeks)"), test: a => a.declining, cols: ["trend_4w", "active_7d"] }
            : { id: "growing", l: label("are growing (weekly actives up 10%+ over 4 weeks)", "is growing (weekly actives up 10%+ over 4 weeks)"), test: a => a.growing, cols: ["trend_4w", "active_7d"] });
          out.defaultSort = { key: "trend_4w", dir: dir === "down" ? "asc" : "desc" };
        }
      }
    }

    // renewals
    if (/renew/.test(qn)) {
      let days = 90;
      const r = qn.match(/(within|in|next|over the next|coming)\s+(the\s+)?(next\s+)?(\d+)\s*(days?|weeks?|months?)/);
      if (r) days = +r[4] * (/week/.test(r[5]) ? 7 : /month/.test(r[5]) ? 30 : 1);
      else if (/this month|next month/.test(qn)) days = /next month/.test(qn) ? 60 : 30;
      else if (/this quarter|next quarter/.test(qn)) days = /next quarter/.test(qn) ? 180 : 90;
      else if (/this year|next year|12 months/.test(qn)) days = 365;
      out.filters.push({ id: "renewal", l: label(`renew within ${days} days`, `renews within ${days} days`), test: a => isNum(a.renewing_in_days) && a.renewing_in_days >= 0 && a.renewing_in_days <= days, cols: ["renewal", "renewing_in_days"] });
      out.defaultSort = out.defaultSort || { key: "renewing_in_days", dir: "asc" };
      out.scope = out.scope === "consuming" && !/(using|consuming|active)/.test(qn) ? "consuming" : out.scope;
    }

    // utilisation
    if (/low utili|under[- ]?utili|unused seats|shelfware|idle seats|poor utili|wasted seats/.test(qn)) out.filters.push({ id: "low_util", l: label("have low seat utilization (<30%, 10+ seats)", "has low seat utilization (<30%, 10+ seats)"), test: a => a.low_utilization, cols: ["utilization", "assigned", "active_28d"] });
    else if (/high utili|well[- ]utili|fully utili|strong utili/.test(qn)) out.filters.push({ id: "high_util", l: label("have high seat utilization (80%+)", "has high seat utilization (80%+)"), test: a => n0(a.utilization) >= 0.8, cols: ["utilization", "assigned"] });

    // surfaces
    let qs = qn;
    SURFACES.forEach(s => {
      const mm = qs.match(s.re);
      if (!mm) return;
      const neg = negatedAt(qs, mm.index);
      const uses = a => (a.integrations || []).some(i => i.surface === s.name && (n0(i.l28) > 0 || n0(i.u28) > 0));
      out.filters.push({ id: "surface", surface: s.name, l: neg ? label(`don't use ${s.name}`, `doesn't use ${s.name}`) : label(`use ${s.name}`, `uses ${s.name}`), test: neg ? a => !uses(a) : uses, cols: neg ? ["top_surface"] : ["surface:" + s.name] });
      if (!neg) out.defaultSort = out.defaultSort || { key: "surface:" + s.name, dir: "desc" };
      qs = qs.replace(s.re, " SURF ");
    });

    // models
    const qdash = qs.replace(/\s+/g, "-");
    let modelHit = MODEL_NAMES.filter(mn => qdash.includes(mn) || qs.includes(mn));
    let modelLabel = modelHit.join(" or ");
    if (!modelHit.length) {
      const fam = MODEL_FAMILIES.find(f => wordRe(f).test(qs));
      if (fam) { modelHit = MODEL_NAMES.filter(mn => mn.includes(fam)); modelLabel = fam + " models"; }
    }
    if (modelHit.length && out.intent !== "models") {
      const idx = qs.search(wordRe(modelLabel.split(" ")[0]));
      const neg = idx >= 0 && negatedAt(qs, idx);
      const uses = a => (a.models || []).some(x => modelHit.includes(x.model) && n0(x.l28) > 0);
      out.filters.push({ id: "model", models: modelHit, l: neg ? label(`don't use ${modelLabel}`, `doesn't use ${modelLabel}`) : label(`use ${modelLabel}`, `uses ${modelLabel}`), test: neg ? a => !uses(a) : uses, cols: neg ? ["top_model"] : ["model:" + modelHit.join("|")] });
      if (!neg) out.defaultSort = out.defaultSort || { key: "model:" + modelHit.join("|"), dir: "desc" };
    }

    // attributes (region, segment, industry, territory)
    ATTRS.forEach(at => {
      if (at.key.length >= 4 && wordRe(at.key).test(qn) && !new RegExp(esc(at.key) + " seats").test(qn)) {
        if (out.filters.some(f => f.id === "attr" && f.field === at.field && f.value === at.value)) return;
        out.filters.push({ id: "attr", field: at.field, value: at.value, l: label(`are in ${at.field} ${at.value}`, `is in ${at.field} ${at.value}`), test: a => a[at.field] === at.value, cols: [at.field] });
      }
    });

    // ranking
    const rq = qn.replace(/\bat (least|most)\b/g, " ");
    const nm = rq.match(/\b(top|bottom|first|last)\s+(\d+)\b(?!\s*(days?|weeks?|months?))/) || rq.match(/\b(\d+)\s+(accounts|customers|companies)\b/);
    if (nm) out.n = +(nm[2] && /^\d+$/.test(nm[2]) ? nm[2] : nm[1]);
    if (!out.rank) {
      if (/\b(top|most|highest|biggest|largest|best|max(imum)?|leading|heaviest|greatest)\b/.test(rq) && out.intent == null) out.rank = { dir: "desc" };
      else if (/\b(bottom|least|lowest|smallest|fewest|worst|min(imum)?|lightest|weakest)\b/.test(rq) && out.intent == null) out.rank = { dir: "asc" };
      if (out.rank) { out.rank.metric = out.metric || MET.active_7d; out.rank.key = out.rank.metric.k; }
      if (out.rank && out.rank.metric.k === "health" && /worst/.test(rq)) out.rank.dir = "asc";
    }
    if (out.rank && !out.n) out.n = 10;

    // aggregates
    const hm = qn.match(/how many\s+(\S+)/);
    if (hm) out.agg = /^(accounts?|customers?|companies|orgs?|organi[sz]ations?|of|are|do|does|have|is|my|clients?)$/.test(hm[1]) || !out.metric ? "count" : "sum";
    else if (/\b(number of|count( of)?)\s+(accounts|customers|companies)|\bcount\b/.test(qn)) out.agg = "count";
    else if (/\b(total|sum|combined|overall|in total|altogether|aggregate)\b/.test(qn) && out.metric) out.agg = "sum";
    else if (/\b(average|avg|mean|typical|median)\b/.test(qn) && out.metric) out.agg = /median/.test(qn) ? "median" : "avg";

    if (/^(is|are|does|do|did|has|have|was|were)\b/.test(q) && out.accounts.length) out.yesno = true;
    if (out.accounts.length) out.scope = "all";
    else if (out.scope === "consuming" && out.filters.some(f => f.id === "renewal") && !/(using|consuming|active|copilot)/.test(qn)) out.scope = "all";
    out.week = lastWeek && out.metric && out.metric.wk ? 1 : 0;
    return out;
  }

  // ---------------------------------------------------------------- execute
  const SCOPE = {
    consuming: { test: a => a.consuming, pl: "consuming accounts", sg: "consuming account", desc: "consuming Copilot in the last 90 days" },
    not_consuming: { test: a => !a.consuming, pl: "owned accounts not consuming Copilot", sg: "owned account not consuming Copilot", desc: "no Copilot users, seats or UBB spend in 90 days" },
    all: { test: () => true, pl: "owned accounts", sg: "owned account", desc: "all accounts you own in Salesforce" },
  };
  function colGetter(key) {
    if (key.startsWith("surface:")) { const s = key.slice(8); return a => (a.integrations || []).filter(i => i.surface === s).reduce((t, i) => t + n0(i.l28), 0); }
    if (key.startsWith("model:")) { const ms = key.slice(6).split("|"); return a => (a.models || []).filter(x => ms.includes(x.model)).reduce((t, x) => t + n0(x.l28), 0); }
    if (MET[key]) return metricGetter(MET[key], 0);
    return a => a[key];
  }
  const COL_LABEL = { name: "Account", health_cat: "Health", health_prev: "Health last week", health: "Health score", active_7d: "Active 7d", active_28d: "Active 28d", assigned: "Assigned",
    contracted_seats: "Contracted", utilization: "Utilization", at_risk: "At risk", gross_7d: "UBB 7d", gross_28d: "UBB 28d", gross_90d: "UBB 90d", mtd_gross: "MTD", gross_run_rate_month: "Month run-rate",
    billable_7d: "Billable 7d", aiu_7d: "AI units 7d", total_arr: "GitHub ARR", acceptance_28d: "Acceptance", trend_4w: "4-wk trend", renewal: "Renewal", renewing_in_days: "Days to renewal",
    wow_a7: "Δ active 7d", wow_a28: "Δ active 28d", wow_assigned: "Δ assigned", wow_gross: "Δ UBB 7d", wow_at_risk: "Δ at risk", wow_health: "Δ health", wow_aiu: "Δ AI units", wow_acceptance: "Δ acceptance",
    top_surface: "Top surface", top_model: "Top model", ghe_seats: "GHE seats", region: "Region", segment: "Segment", industry: "Industry", territory: "Territory" };
  const COL_FMT = { gross_7d: "money", gross_28d: "money", gross_90d: "money", mtd_gross: "money", gross_run_rate_month: "money", billable_7d: "money", total_arr: "money", wow_gross: "moneyDelta",
    utilization: "pct", acceptance_28d: "pct", trend_4w: "pctSigned", health: "num2", wow_health: "num2Delta", wow_a7: "delta", wow_a28: "delta", wow_assigned: "delta", wow_at_risk: "deltaInv", wow_aiu: "delta", wow_acceptance: "ppDelta" };
  function col(key) {
    let lbl = COL_LABEL[key] || key, fmt = COL_FMT[key] || "auto";
    if (key.startsWith("surface:")) { lbl = key.slice(8) + " $ 28d"; fmt = "money"; }
    if (key.startsWith("model:")) { const ms = key.slice(6).split("|"); lbl = (ms.length > 1 ? "Selected models" : ms[0]) + " $ 28d"; fmt = "money"; }
    return { key, label: lbl, fmt, get: colGetter(key) };
  }
  function fmtVal(v, f) {
    if (v == null || (typeof v === "number" && !isFinite(v))) return "—";
    if (typeof v !== "number") return String(v);
    if (f === "money") return "$" + Math.round(v).toLocaleString();
    if (f === "pct") return Math.round(v * 100) + "%";
    if (f === "pctSigned") return (v > 0 ? "+" : "") + Math.round(v * 100) + "%";
    if (f === "num2") return v.toFixed(2);
    return Math.round(v * 10) / 10 === Math.round(v) ? Math.round(v).toLocaleString() : (Math.round(v * 10) / 10).toLocaleString();
  }
  const clean = n => String(n).replace(/\.+$/, "");
  const listNames = (rows, getV, f) => rows.map(a => getV ? `${clean(a.name)} (${fmtVal(getV(a), f)})` : clean(a.name)).join(", ");

  function suggestions() {
    return ["Which accounts are not active this week?", "Top 5 accounts by spend", "Which accounts have Red health?", "Who is renewing in the next 90 days?",
      "Which accounts use Copilot CLI?", "Biggest drop in weekly active users", "Accounts with more than 50 active users", "How many accounts are declining?",
      "Total UBB spend this week", "What changed this week?", "Which models are used most?", "Accounts not using Copilot yet"];
  }

  function ask(question) {
    const p = parse(question);
    const asof = Object.values(D.asof || {}).sort().pop() || D.data_date;
    const base = { question, asof, interpreted: [] };
    if (!p.q) return Object.assign(base, { ok: false, type: "empty", answer: "Type a question about your accounts.", suggestions: suggestions() });
    if (p.intent === "help") return Object.assign(base, { ok: true, type: "help", answer: "Try one of these. Filters, rankings and counts can be combined, e.g. \"Red health accounts using Copilot CLI with more than 20 users\".", suggestions: suggestions() });

    if (p.intent === "changes") {
      let ch = (D.changes || []).slice();
      const interp = ["this week's change feed"];
      if (p.accounts.length) { const ids = new Set(p.accounts.map(a => a.id)); ch = ch.filter(c => ids.has(c.id)); interp.push("for " + p.accounts.map(a => a.name).join(", ")); }
      if (/negative|\bbad\b|concern|worr|risk|problem|red flag|issues?|declin|drop/.test(p.q)) { ch = ch.filter(c => c.severity === "bad" || c.severity === "warn"); interp.push("negative/watch only"); }
      else if (/positive|\bgood\b|wins?|improv|growth|grew/.test(p.q)) { ch = ch.filter(c => c.severity === "good"); interp.push("positive only"); }
      const KINDS = { renewal: /renew/, spend: /spend|\$|cost|usage/, health: /health/, surface: /surface|feature/, contract: /contract|seats?/, stopped: /stopped|inactive/, risk: /at[- ]risk/, active_users: /active users/ };
      const kinds = Object.keys(KINDS).filter(k => KINDS[k].test(p.q.replace(/what changed|changes/g, "")));
      if (kinds.length) { ch = ch.filter(c => kinds.includes(c.kind)); interp.push("types: " + kinds.join(", ")); }
      const order = { bad: 0, warn: 1, good: 2, info: 3 };
      ch.sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
      const top = ch.slice(0, 5).map(c => `${c.account}: ${c.text}`).join("; ");
      return Object.assign(base, { ok: true, type: "changes", interpreted: interp, rows: ch, answer: ch.length ? `${ch.length} change${ch.length === 1 ? "" : "s"} this week. ${ch.length > 5 ? "Top: " : ""}${top}.` : "No matching changes this week." });
    }

    if (p.intent === "surfaces" || p.intent === "models") {
      const isS = p.intent === "surfaces";
      let rows;
      const pool = p.accounts.length ? p.accounts : A.filter(a => a.consuming);
      const agg = {};
      pool.forEach(a => (isS ? a.integrations : a.models || []).forEach(i => {
        const k = isS ? i.surface : i.model;
        const r = agg[k] || (agg[k] = { name: k, spend_7d: 0, spend_prev_7d: 0, spend_28d: 0, accounts: 0 });
        r.spend_7d += n0(i.l7); r.spend_prev_7d += n0(i.p7); r.spend_28d += n0(i.l28); if (n0(i.l28) > 0 || n0(i.u28) > 0) r.accounts += 1;
      }));
      rows = Object.values(agg).filter(r => r.spend_28d > 0 || r.accounts > 0);
      const asc = /\b(least|lowest|bottom|fewest)\b/.test(p.q);
      rows.sort((a, b) => (asc ? a.spend_28d - b.spend_28d : b.spend_28d - a.spend_28d));
      const tot = rows.reduce((t, r) => t + r.spend_28d, 0) || 1;
      rows.forEach(r => { r.share = r.spend_28d / tot; r.wow = r.spend_7d - r.spend_prev_7d; });
      const who = p.accounts.length ? p.accounts.map(a => a.name).join(", ") : "your consuming accounts";
      const top = rows.slice(0, 5).map(r => `${r.name} ${Math.round(r.share * 100)}%`).join(", ");
      return Object.assign(base, { ok: true, type: "table", interpreted: [`UBB spend by ${isS ? "surface" : "model"}, last 28 days`, who],
        cols: [{ key: "name", label: isS ? "Surface" : "Model" }, { key: "share", label: "Share 28d", fmt: "pct" }, { key: "spend_28d", label: "28d $", fmt: "money" }, { key: "spend_7d", label: "7d $", fmt: "money" }, { key: "wow", label: "WoW", fmt: "moneyDelta" }, { key: "accounts", label: "Accounts" }],
        rows, answer: rows.length ? `By UBB spend (28d) across ${who}: ${top}.` : "No UBB usage recorded." });
    }

    const hasSignal = p.filters.length || p.metric || p.rank || p.agg || p.accounts.length || p.scope !== "consuming";
    if (!hasSignal) return Object.assign(base, { ok: false, type: "unknown", answer: "I couldn't map that to the dashboard's data. Try one of these, or copy the question to Copilot chat.", suggestions: suggestions() });

    const scope = SCOPE[p.scope];
    let pool = A.filter(scope.test);
    const interp = [`${scope.pl} (${scope.desc})`];
    if (p.accounts.length) { const ids = new Set(p.accounts.map(a => a.id)); pool = pool.filter(a => ids.has(a.id)); interp.push("account: " + p.accounts.map(a => a.name).join(", ")); }
    let rows = pool;
    p.filters.forEach(f => { rows = rows.filter(f.test); interp.push(f.l.pl.replace(/^don't use /, "not using ").replace(/^(are|have|use|renew|were|had|don't) /, m => ({ "are ": "", "have ": "with ", "use ": "using ", "renew ": "renewing ", "were ": "", "had ": "", "don't ": "not " }[m] || m))); });
    const w = p.week;
    const primary = p.rank ? p.rank.metric : p.metric;
    if (w) interp.push("metric for last week");

    // yes/no about a named account
    if (p.yesno && p.accounts.length === 1 && p.filters.length) {
      const a = p.accounts[0], yes = rows.length === 1;
      const f = p.filters.map(x => x.l.sg).join(" and ");
      const cols = ["name"].concat(...p.filters.map(x => x.cols)).filter((v, i, s) => s.indexOf(v) === i).slice(0, 6).map(col);
      return Object.assign(base, { ok: true, type: "accounts", interpreted: interp, cols, rows: [a], answer: `${yes ? "Yes" : "No"}: ${a.name} ${yes ? f : "does not match: " + f}.` });
    }

    // account snapshot
    if (p.accounts.length && !p.filters.length && !p.rank && !p.agg) {
      const cols = ["name", "health_cat", "active_7d", "wow_a7", "active_28d", "assigned", "utilization", "at_risk", "gross_7d", "wow_gross", "top_surface", "top_model", "renewal"].map(col);
      const extra = p.metric ? [col(p.metric.k)] : [];
      const ans = rows.map(a => {
        if (p.metric) return `${a.name}: ${p.metric.label} ${fmtVal(metricGetter(p.metric, w)(a), p.metric.f)}${p.metric.wow && isNum(a[p.metric.wow]) ? ` (${a[p.metric.wow] >= 0 ? "+" : ""}${fmtVal(a[p.metric.wow], p.metric.f)} WoW)` : ""}`;
        const bits = [a.consuming ? (a.active_this_week ? "active this week" : "not active this week") : "not consuming Copilot"];
        if (isNum(a.active_7d)) bits.push(`${a.active_7d} weekly active${isNum(a.wow_a7) ? ` (${a.wow_a7 >= 0 ? "+" : ""}${a.wow_a7} WoW)` : ""}`);
        if (isNum(a.assigned)) bits.push(`${a.assigned} assigned seats`);
        if (a.health_cat) bits.push(`${a.health_cat} health`);
        if (isNum(a.gross_7d)) bits.push(`$${Math.round(a.gross_7d).toLocaleString()} UBB spend last 7d`);
        if (a.top_surface) bits.push(`top surface ${a.top_surface}`);
        return `${a.name}: ${bits.join(", ")}`;
      }).join(". ");
      return Object.assign(base, { ok: true, type: "accounts", interpreted: interp, cols: extra.concat(cols).filter((c, i, s) => s.findIndex(x => x.key === c.key) === i), rows, answer: ans + "." });
    }

    // aggregates
    if (p.agg && p.agg !== "count" && primary) {
      const g = metricGetter(primary, w), vals = rows.map(g).filter(isNum);
      let v = vals.reduce((t, x) => t + x, 0), word = "Total";
      if (p.agg === "avg") { v = vals.length ? v / vals.length : null; word = "Average"; }
      if (p.agg === "median") { const s = vals.slice().sort((a, b) => a - b); v = s.length ? (s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2) : null; word = "Median"; }
      if (primary.f === "pct" && p.agg === "sum") { v = vals.length ? v / vals.length : null; word = "Average"; }
      const filt = p.filters.length ? ` that ${p.filters.map(f => f.l.pl).join(" and ")}` : "";
      interp.push(`${word.toLowerCase()} of ${primary.label}${w ? " (last week)" : ""}`);
      const cols = ["name", primary.k].concat(...p.filters.map(f => f.cols)).filter((x, i, s) => s.indexOf(x) === i).slice(0, 6).map(col);
      rows = rows.slice().sort((a, b) => n0(g(b)) - n0(g(a)));
      return Object.assign(base, { ok: true, type: "accounts", interpreted: interp, cols, rows, answer: `${word} ${primary.label}${w ? " last week" : ""} across ${rows.length} ${rows.length === 1 ? scope.sg : scope.pl}${filt}: ${fmtVal(v, primary.f)}.` });
    }

    // sorting
    let sortKey, dir;
    if (p.rank) { sortKey = p.rank.key; dir = p.rank.dir; }
    else if (p.defaultSort) { sortKey = p.defaultSort.key; dir = p.defaultSort.dir; }
    else if (p.metric) { sortKey = p.metric.k; dir = "desc"; }
    else { sortKey = p.inactiveUsed ? "gross_28d" : "active_28d"; dir = "desc"; }
    const sg = sortKey === (primary && primary.k) && primary ? metricGetter(primary, w) : colGetter(sortKey);
    rows = rows.slice().sort((a, b) => {
      const x = sg(a), y = sg(b);
      if (!isNum(x) && !isNum(y)) return 0; if (!isNum(x)) return 1; if (!isNum(y)) return -1;
      return dir === "asc" ? x - y : y - x;
    });
    const total = rows.length;
    if (p.rank) {
      rows = rows.filter(a => isNum(sg(a)));
      rows = rows.slice(0, p.n);
      interp.push(`${p.rank.change ? (p.rank.change === "down" ? "biggest drops" : "biggest increases") + " in " + p.rank.metric.label + " WoW" : (dir === "desc" ? "top " : "bottom ") + p.n + " by " + p.rank.metric.label}`);
    } else if (p.n) rows = rows.slice(0, p.n);

    let keys = ["name"];
    if (primary) keys.push(primary.k);
    if (p.rank && p.rank.change) keys.push(p.rank.key);
    p.filters.forEach(f => keys.push(...f.cols));
    if (sortKey && !keys.includes(sortKey)) keys.push(sortKey);
    ["active_7d", "gross_7d", "health_cat"].forEach(k => keys.length < 5 && keys.push(k));
    keys = keys.filter((x, i, s) => s.indexOf(x) === i).slice(0, 7);
    const cols = keys.map(col);

    const filt = p.filters.map(f => f.l);
    const predicate = n => filt.map(l => (n === 1 ? l.sg : l.pl)).join(" and ");
    let answer;
    const vcol = primary ? { g: metricGetter(primary, w), f: primary.f } : null;
    if (p.rank) {
      const g = p.rank.change ? (a => a[p.rank.key]) : sg;
      const f = p.rank.change ? p.rank.metric.f : (primary && primary.f);
      const what = p.rank.change ? `biggest week-over-week ${p.rank.change === "down" ? "drops" : "increases"} in ${p.rank.metric.label}` : `${dir === "desc" ? "Top" : "Bottom"} ${rows.length} by ${p.rank.metric.label}${w ? " (last week)" : ""}`;
      const extraF = p.filters.filter(x => x.id !== "wow_dir");
      answer = rows.length ? `${p.rank.change ? what[0].toUpperCase() + what.slice(1) : what} among ${scope.pl}${extraF.length ? " that " + extraF.map(x => x.l.pl).join(" and ") : ""}: ${listNames(rows, g, f)}.` : `No ${scope.pl} match.`;
    } else if (p.agg === "count" || rows.length > 0) {
      const n = total;
      const head = n === 0 ? `None of your ${scope.pl}${filt.length ? " " + predicate(2) : " match"}` : `${n} ${n === 1 ? scope.sg : scope.pl}${filt.length ? " " + predicate(n) : ""}`;
      answer = head + (n > 0 && n <= 8 ? `: ${listNames(rows.slice(0, 8), vcol && p.metric !== MET.active_7d ? vcol.g : null, vcol && vcol.f)}` : "") + ".";
      if (p.scope === "consuming" && !filt.length && p.agg === "count") answer = `${n} of ${A.length} owned accounts are consuming Copilot (last 90 days).`;
    } else {
      answer = `None of your ${scope.pl} ${filt.length ? predicate(2) : "match"}.`;
    }
    return Object.assign(base, { ok: true, type: "accounts", interpreted: interp, cols, rows, total, answer });
  }

  return { ask, parse, suggestions, fmtVal };
}
if (typeof module !== "undefined") module.exports = AskEngine;
