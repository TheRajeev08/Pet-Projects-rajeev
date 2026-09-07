import { createServer } from "node:http";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { readFile, writeFile, createReadStream, existsSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { homedir } from "node:os";
import { joinSession, createCanvas } from "@github/copilot-sdk/extension";

const execFileAsync = promisify(execFile);
const root = new URL(".", import.meta.url).pathname;
const servers = new Map();

// The Python is owned by the skill, so there is exactly one copy of the play logic. This
// extension runs from three places -- the dev folder, a repo checkout, and the user
// extensions directory -- so resolve the scripts instead of assuming a layout.
function scriptCandidates(name) {
    const rel = join("skills", "fy27-territory-plan", "scripts", name);
    const paths = [
        join(root, "plugin", rel),
        join(root, rel),
        join(root, "..", "..", rel),
        join(root, "scripts", name),
        join(root, name),
    ];
    const pluginsRoot = join(homedir(), ".copilot", "installed-plugins");
    try {
        for (const owner of readdirSync(pluginsRoot)) {
            paths.push(join(pluginsRoot, owner, rel));
            try {
                for (const plugin of readdirSync(join(pluginsRoot, owner))) paths.push(join(pluginsRoot, owner, plugin, rel));
            } catch { /* not a directory */ }
        }
    } catch { /* no installed plugins on this machine */ }
    return paths;
}

function scriptPath(name) {
    return scriptCandidates(name).find(existsSync) || join(root, name);
}

function htmlFor(report) {
    const safe = report ? JSON.stringify(report).replace(/</g, "\\u003c") : "null";
    return `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>FY27 Territory Plan</title>
    <style>
    :root{font:14px system-ui;color:#f0f6fc;background:#0d1117;color-scheme:dark}*{box-sizing:border-box}body{margin:0;padding:32px;max-width:1480px;margin-inline:auto}
    .hero{background:linear-gradient(135deg,#161b22,#1f6feb);color:white;border:1px solid #30363d;border-radius:22px;padding:28px 32px;box-shadow:0 16px 35px #0008}.hero-head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.hero h1{font-size:34px;margin:0 0 7px}.hero p{margin:0;color:#c9d1d9}.eyebrow{text-transform:uppercase;letter-spacing:.12em;font-size:11px;font-weight:700;color:#79c0ff}.top-upload{white-space:nowrap}
    h2{font-size:21px;margin:28px 0 13px}.muted{color:#8b949e}.section{margin:25px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(170px,1fr));gap:15px}
    .card{background:#161b22;border:1px solid #30363d;border-radius:16px;padding:19px;box-shadow:0 5px 16px #0005}.metric{border-top:4px solid #58a6ff}.metric:nth-child(2){border-top-color:#bc8cff}.metric:nth-child(3){border-top-color:#3fb950}.label{font-weight:650;color:#c9d1d9}.num{font-size:34px;font-weight:800;margin:8px 0 2px}.sub{font-size:12px;color:#8b949e}.clickable{text-align:left}.clickable:hover{transform:translateY(-2px);border-color:#58a6ff;box-shadow:0 10px 24px #0009}.signal-row{display:flex;gap:10px;flex-wrap:wrap}.signal{border:1px solid #30363d;border-radius:11px;background:#161b22;padding:10px 13px;color:#c9d1d9;cursor:pointer;display:flex;gap:10px;align-items:center}.signal span{font-size:16px;font-weight:800;color:#58a6ff}
    .upload{border:2px dashed #6e7681;text-align:center;padding:65px 20px;border-radius:18px;background:#161b22}.upload.drag{border-color:#58a6ff;background:#1c2b41}input[type=file]{display:none}
    button,.button{border:1px solid #30363d;background:#21262d;border-radius:9px;padding:10px 14px;cursor:pointer;display:inline-block;font-weight:650;color:#f0f6fc}button.primary,.button.primary{background:#238636;color:white;border-color:#2ea043}.actions{display:flex;gap:10px;flex-wrap:wrap}
    .scroll{overflow:auto;border:1px solid #30363d;border-radius:14px;background:#161b22;box-shadow:0 4px 14px #0006}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #21262d;vertical-align:top}th{background:#21262d;color:#8b949e;font-size:12px;text-transform:uppercase;letter-spacing:.04em;position:sticky;top:0}tbody tr{cursor:pointer}tbody tr:hover{background:#1c2128}
    .play{position:relative;overflow:hidden;cursor:pointer;text-decoration:none;color:inherit}.play:after{content:"";position:absolute;right:-20px;bottom:-32px;width:100px;height:100px;border-radius:50%;background:#ffffff0a}.play.innovate{border-top:5px solid #bc8cff}.play.trust{border-top:5px solid #3fb950}.play.scale{border-top:5px solid #f0883e}.play.unclassified{border-top:5px solid #6e7681;opacity:.92}.play:hover{transform:translateY(-2px);box-shadow:0 10px 24px #0009}.play.selected{outline:3px solid #58a6ff;outline-offset:2px}.play-title{font-size:17px;font-weight:750}.pill{display:inline-block;padding:4px 9px;border-radius:999px;background:#1f3b5b;color:#79c0ff;font-size:12px;margin:2px;font-weight:650}.small{font-size:12px}.error{color:#ff7b72;background:#3d1f24;border:1px solid #f85149;padding:12px;border-radius:9px}
    .filter{background:#1f3b5b;color:#79c0ff;border-radius:999px;padding:7px 11px;font-size:12px;font-weight:700}.empty{padding:28px;text-align:center}.arrow{float:right;color:#58a6ff}.bar-track{height:10px;background:#30363d;border-radius:999px;margin-top:12px;overflow:hidden}.bar-fill{height:100%;border-radius:999px}.bar-fill.innovate{background:#bc8cff}.bar-fill.trust{background:#3fb950}.bar-fill.scale{background:#f0883e}.bar-fill.unclassified{background:#6e7681}.activity-priority{color:#3fb950;font-weight:700}.activity-muted{color:#8b949e}.governance{border-left:5px solid #d29922;background:#2b2415;padding:16px 18px;border-radius:12px;margin-top:18px}.score{font-size:20px;font-weight:800}.unknown{color:#d29922;font-weight:700}.definition-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.definition{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:14px}.hidden{display:none}@media(max-width:1000px){.grid,.definition-grid{grid-template-columns:1fr 1fr}}@media(max-width:800px){body{padding:16px}.grid,.definition-grid{grid-template-columns:1fr}.hero h1{font-size:28px}}
    </style></head><body><div id="app"></div><script>
    const initial=${safe};let report=initial,params=new URLSearchParams(location.search),filter=params.get("play")||null,view=params.get("view")||"all";const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    function uploadView(message=""){document.getElementById("app").innerHTML=\`<div class="hero"><div class="eyebrow">FY27 Revenue Planning</div><h1>Territory Plan</h1><p>Turn your SuperDash CSV into an actionable Innovate, Trust, and Scale book of business.</p></div>\${message?\`<div class="error section">\${esc(message)}</div>\`:""}<div id="drop" class="upload section"><h2>Upload your SuperDash CSV</h2><p class="muted">We will de-duplicate accounts, collate parent/child records, and build your play plan.</p><label class="button primary" for="file">Choose CSV or XLSX</label><input id="file" type="file" accept=".csv,.xlsx"><p class="muted small">or drag and drop your file here</p></div>\`;const drop=document.getElementById("drop"),file=document.getElementById("file");file.onchange=()=>file.files[0]&&send(file.files[0]);["dragenter","dragover"].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add("drag")}));["dragleave","drop"].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove("drag")}));drop.addEventListener("drop",ev=>ev.dataTransfer.files[0]&&send(ev.dataTransfer.files[0]))}
    async function send(file){const drop=document.getElementById("drop");if(drop)drop.innerHTML="<h2>Building your plan…</h2><p class='muted'>Normalizing accounts and calculating play signals.</p>";try{const r=await fetch("/api/analyze",{method:"POST",headers:{"Content-Type":"application/octet-stream","X-File-Name":encodeURIComponent(file.name)},body:await file.arrayBuffer()});const data=await r.json();if(!r.ok)throw new Error(data.error||"Upload failed");location.reload()}catch(e){uploadView(e.message)}}
    function selectView(next){filter=["Innovate","Trust","Scale","Unclassified"].includes(next)?next:null;view=next==="duplicates"?"duplicates":next==="collated"?"collated":"all";location.href="?"+(filter?"play="+encodeURIComponent(filter)+"&":"")+"view="+view}
    function dashboard(){const s=report.stats,activity=report.activity||{},gov=report.governance||{},baseAccounts=report.accounts.filter(a=>!filter||a.primaryPlay===filter),duplicateRows=(report.duplicateAccounts||[]).map(d=>({name:d.name,salesforceId:"",primaryPlay:"Duplicate",plays:[],renewal:"",consumption:d.rows+" rows: "+d.variants.join(" | "),evidence:[],discoveryGaps:[],winPlan:"Parent/child or repeated-name records collated into one account.",potentialScore:0,engagementScore:null,engagementState:"Review",executionReadiness:0,nextAction:{owner:"RevOps",persona:"Account owner",action:"Resolve the duplicate source records.",exitCriteria:"One governed account record."},activity:{status:"not applicable",tier:"Review",twoWay:false,lastActivity:"",reason:"Duplicate group requires source-record review."}})),accounts=view==="duplicates"?duplicateRows:baseAccounts,signals=report.portfolioSignals||{},maxPlay=Math.max(...report.playSummary.map(p=>p.accounts),1);document.getElementById("app").innerHTML=\`<div class="hero"><div class="hero-head"><div><div class="eyebrow">FY27 Revenue Planning</div><h1>Territory Plan</h1><p>\${esc(report.sourceName)} · generated \${esc(report.generatedAt)}</p></div><div><button class="button primary top-upload" type="button" onclick="document.getElementById('top-file').click()">Upload new CSV</button><input id="top-file" type="file" accept=".csv,.xlsx" onchange="this.files[0]&&send(this.files[0])"></div></div></div><div class="governance"><b>Decision support—not forecast or territory-allocation authority.</b><div class="small muted">\${esc(gov.classification||"Scores are hypotheses that require seller validation.")}</div></div>
    <div class="section"><h2>Upload summary</h2><div class="grid"><button class="card metric clickable" onclick="selectView('all')"><div class="label">Source Rows</div><div class="num">\${s.sourceRows}</div><div class="sub">\${s.excludedRows} excluded · full collated view →</div></button><button class="card metric clickable" onclick="selectView('duplicates')"><div class="label">Collated Groups</div><div class="num">\${s.parentChildGroups}</div><div class="sub">\${s.exactDuplicateGroups} exact dupes · \${s.duplicateRows} rows collapsed →</div></button><button class="card metric clickable" onclick="selectView('collated')"><div class="label">De-duplicated Accounts</div><div class="num">\${s.collatedAccounts}</div><div class="sub">normalized account view →</div></button><button class="card metric clickable" onclick="selectView('Unclassified')"><div class="label">Unclassified</div><div class="num">\${s.unclassifiedAccounts}</div><div class="sub">no qualifying play signal · discovery queue →</div></button></div>\${s.nameConflicts?\`<p class="small muted">\${s.nameConflicts} account name(s) map to more than one Salesforce ID and were kept separate rather than merged.</p>\`:""}</div>
    <div class="section"><h2>Portfolio product signals <span class="muted small">from uploaded SuperDash data</span></h2><div class="signal-row">\${Object.entries(signals).map(([k,v])=>\`<button class="signal" onclick="selectView('collated')"><b>\${esc(k)}</b><span>\${k==='LM consumption'?'$'+Math.round(v).toLocaleString():Math.round(v).toLocaleString()}</span></button>\`).join("")}</div></div>
    <div class="section"><h2>Play distribution <span class="muted small">primary-play account counts</span></h2><div class="grid">\${report.playSummary.map(p=>\`<a class="card play \${p.play.toLowerCase()} \${filter===p.play?"selected":""}" href="?play=\${encodeURIComponent(p.play)}&view=all"><div class="play-title">\${esc(p.play)} <span class="arrow">→</span></div><div class="num">\${p.accounts}</div><div class="sub">\${p.play==="Unclassified"?"no qualifying product signal · discovery queue":\`\${p.qualifiedAccounts} total qualified · \${p.activityPrioritized||0} activity-prioritized\`}</div><div class="bar-track"><div class="bar-fill \${p.play.toLowerCase()}" style="width:\${Math.round(p.accounts/maxPlay*100)}%"></div></div><span class="small">\${p.link?\`<a href="\${esc(p.link)}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">Open Seismic play ↗</a>\`:"Not a play — run discovery before assigning"}</span></a>\`).join("")}</div></div>
    <div class="section"><h2>Salesforce engagement coverage</h2><div class="grid"><div class="card"><div class="label">Matched activity</div><div class="num">\${activity.enrichedAccounts||0}</div><div class="sub">\${Number(activity.coveragePct||0).toFixed(1)}% of \${report.accountCount} accounts · \${activity.windowDays||90}-day window</div></div><div class="card"><div class="label">Two-way evidence</div><div class="num">\${activity.twoWayAccounts||0}</div><div class="sub">verified inbound and outbound evidence</div></div><div class="card"><div class="label">Engagement unknown</div><div class="num">\${activity.unknownAccounts??report.accountCount}</div><div class="sub">no match is not the same as cold · as of \${esc(activity.asOf||"not available")}</div></div></div></div>
    <div class="section"><h2>Governed decision signals</h2><div class="definition-grid"><div class="definition"><b>Potential</b><div class="small muted">\${esc(gov.potentialDefinition||"Relative whitespace proxy.")}</div></div><div class="definition"><b>Engagement</b><div class="small muted">\${esc(gov.engagementDefinition||"Salesforce activity evidence; unmatched is unknown.")}</div></div><div class="definition"><b>Execution readiness</b><div class="small muted">\${esc(gov.readinessDefinition||"Play evidence requiring seller validation.")}</div></div></div></div>
    <div class="section" id="account-drilldown"><div class="actions"><a class="button primary" href="/download">Download detailed Excel</a>\${filter||view!=="all"?\`<button onclick="selectView('all')">Back to all accounts</button>\${filter?\`<span class="filter">Primary: \${esc(filter)}</span>\`:""}\`:""}</div><h2>\${view==="duplicates"?"Duplicate account groups":filter?esc(filter):view==="collated"?"De-duplicated account plan":"Account plan"} <span class="muted small">\${accounts.length} accounts · ordered by two-way evidence, potential, engagement, then readiness</span></h2><div class="scroll"><table><thead><tr><th>Account</th><th>Primary play</th><th>Renewal / capacity</th><th>Potential proxy</th><th>Engagement</th><th>Execution readiness</th><th>Product signals</th><th>Next action</th></tr></thead><tbody>\${accounts.map(a=>{const ac=a.activity||{},known=ac.status==="enriched",action=a.nextAction||{};return \`<tr onclick="this.querySelector('.details').classList.toggle('hidden')"><td><b>\${esc(a.name)}</b><div class="muted small">\${esc(a.salesforceId)}</div><div class="details hidden small"><br><b>Potential rationale:</b> \${esc(a.revenueReason||"Duplicate group requires source-record review.")}<br><b>Evidence:</b> \${esc(a.evidence.join("; "))}<br><b>Discovery:</b> \${esc(a.discoveryGaps.join("; "))}<br><b>Activity:</b> \${esc(ac.reason||"No activity enrichment")}<br><b>Readiness:</b> \${esc(a.executionReason||"Requires source-record review.")}<br><b>Exit criteria:</b> \${esc(action.exitCriteria||"Seller validation required.")}</div></td><td><span class="pill">\${esc(a.primaryPlay)}</span><div class="small muted">\${a.plays.length} play signal\${a.plays.length===1?"":"s"}</div></td><td>\${esc(a.renewal||"—")}<div class="small muted">\${esc(a.renewalHorizon||"Unknown")}\${a.renewalConflict?" · conflicting dates":""}</div><div class="small muted">\${esc(a.capacityTier||"Unsized")}</div></td><td><div class="score \${a.potentialScore==null?"unknown":""}">\${a.potentialScore==null?"—":Number(a.potentialScore).toFixed(0)}</div><div class="small muted">\${a.potentialScore==null?"no whitespace signal":"relative / 100<br>not ARR or forecast"}</div></td><td class="small \${known?(ac.tier==="Priority"||ac.tier==="High"?"activity-priority":"activity-muted"):"unknown"}">\${known?esc(a.engagementState||ac.tier):"Unknown"}<br>\${known?Number(a.engagementScore||0).toFixed(0)+" / 100":"No matched activity"}<br>\${known?(ac.twoWay?"Two-way":"One-way/no directional proof"):"Not cold"}<br>\${esc(ac.lastActivity||"—")}</td><td><div class="score \${a.executionReadiness==null?"unknown":""}">\${a.executionReadiness==null?"—":Number(a.executionReadiness).toFixed(0)}</div><div class="small muted">\${a.executionReadiness==null?"not play-qualified":"evidence-based / 100"}</div></td><td class="small">\${esc(a.consumption)}</td><td class="small"><b>\${esc(action.owner||"Account owner")}</b><br>\${esc(action.action||a.winPlan)}<br><span class="muted">Persona: \${esc(action.persona||"Validate with seller")}</span></td></tr>\`}).join("")||\`<tr><td colspan="8" class="empty">No accounts match this view.</td></tr>\`}</tbody></table></div></div>\`;}\n        if(report)dashboard();else uploadView();</script></body></html>`;
}

async function analyzeCsv(text, entry, preserveActivity = false) {
    if (!preserveActivity) entry.activityPath = null;
    const inputPath = join(entry.runDir, "uploaded-territory.bin");
    // Write bytes verbatim: a UTF-8 round trip silently corrupts XLSX uploads.
    const payload = Buffer.isBuffer(text) ? text : Buffer.from(text);
    await new Promise((resolve, reject) => writeFile(inputPath, payload, e => e ? reject(e) : resolve()));
    const args = [scriptPath("workbook.py"), inputPath, entry.runDir];
    args.push(entry.activityPath || "");
    if (entry.sourceName) args.push(entry.sourceName);
    const { stdout } = await execFileAsync("python3", args, { maxBuffer: 20 * 1024 * 1024 });
    const result = JSON.parse(stdout);
    entry.reportPath = result.reportPath;
    entry.workbookPath = result.workbookPath;
    return JSON.parse(await new Promise((resolve, reject) => readFile(result.reportPath, "utf8", (e, d) => e ? reject(e) : resolve(d))));
}

async function startServer(instanceId, reportPath, inputPath = join(root, "artifacts", "uploaded-territory.bin")) {
    // Everything hangs off the report's own directory. When a skill opens the canvas on an
    // isolated run, that run's enrichment and workbook must be used -- never the personal cache.
    const runDir = reportPath ? dirname(reportPath) : join(root, "artifacts");
    const entry = { runDir, reportPath, inputPath, activityPath: reportPath ? join(runDir, "salesforce-activity.json") : null, workbookPath: reportPath ? join(runDir, "FY27 Territory Plan.xlsx") : null };
    const server = createServer(async (req, res) => {
        try {
            if (req.method === "POST" && req.url === "/api/analyze") {
                const chunks = []; let size = 0; for await (const chunk of req) { chunks.push(chunk); size += chunk.length; if (size > 15_000_000) throw new Error("Upload is larger than the 15 MB limit."); }
                entry.sourceName = req.headers["x-file-name"] ? decodeURIComponent(req.headers["x-file-name"]) : null;
                const report = await analyzeCsv(Buffer.concat(chunks), entry);
                res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify({ ok: true, stats: report.stats }));
                return;
            }
            if (req.method === "POST" && req.url === "/api/activity") {
                let body = ""; for await (const chunk of req) { body += chunk; if (body.length > 10_000_000) throw new Error("Activity enrichment is larger than the 10 MB limit."); }
                entry.activityPath ||= join(entry.runDir, "salesforce-activity.json");
                await new Promise((resolve, reject) => writeFile(entry.activityPath, body, "utf8", e => e ? reject(e) : resolve()));
                let report;
                if (entry.inputPath.toLowerCase().endsWith(".csv")) {
                    report = await analyzeCsv(await new Promise((resolve, reject) => readFile(entry.inputPath, "utf8", (e, d) => e ? reject(e) : resolve(d))), entry, true);
                } else {
                    const { stdout } = await execFileAsync("python3", [scriptPath("workbook.py"), entry.inputPath, entry.runDir, entry.activityPath], { maxBuffer: 20 * 1024 * 1024 });
                    const result = JSON.parse(stdout);
                    entry.reportPath = result.reportPath; entry.workbookPath = result.workbookPath;
                    report = JSON.parse(await new Promise((resolve, reject) => readFile(result.reportPath, "utf8", (e, d) => e ? reject(e) : resolve(d))));
                }
                res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify({ ok: true, activity: report.activity }));
                return;
            }
            if (req.url === "/download" && entry.workbookPath) {
                res.setHeader("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");
                res.setHeader("Content-Disposition", 'attachment; filename="FY27 Territory Plan.xlsx"');
                createReadStream(entry.workbookPath).pipe(res); return;
            }
            const report = entry.reportPath ? JSON.parse(await new Promise((resolve, reject) => readFile(entry.reportPath, "utf8", (e, d) => e ? reject(e) : resolve(d)))) : null;
            res.setHeader("Content-Type", "text/html; charset=utf-8"); res.end(htmlFor(report));
        } catch (error) {
            res.statusCode = 400; res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify({ error: String(error.message || error) }));
        }
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    return { server, url: `http://127.0.0.1:${server.address().port}/`, ...entry };
}

async function buildPlan(inputPath) {
    // Each build gets its own directory so one teammate's plan never overwrites another's,
    // and nothing CRM-derived is written back into the shared package.
    const { stdout: bootstrap } = await execFileAsync("python3", [scriptPath("new_run.py"), inputPath], { maxBuffer: 4 * 1024 * 1024 });
    const run = JSON.parse(bootstrap);
    const { stdout } = await execFileAsync("python3", [scriptPath("workbook.py"), run.inputPath, run.runDir, "", run.sourceName], { maxBuffer: 20 * 1024 * 1024 });
    return JSON.parse(stdout);
}

const session = await joinSession({
    tools: [{
        name: "build_fy27_territory_plan",
        description: "Build or open the FY27 Territory Plan app from an uploaded CSV or SuperDash XLSX.",
        parameters: { type: "object", properties: { inputPath: { type: "string", description: "Absolute path to the uploaded CSV or SuperDash XLSX file." } }, required: ["inputPath"] },
        handler: async ({ inputPath }) => {
            const result = await buildPlan(inputPath);
            await session.rpc.canvas.open({ canvasId: "fy27-territory-plan", instanceId: "fy27-territory-plan", input: { reportPath: result.reportPath, inputPath } });
            return `Opened the FY27 Territory Plan app. Workbook: ${result.workbookPath}. Accounts: ${result.accountCount}.`;
        },
    }, {
        name: "enrich_fy27_territory_plan_activity",
        description: "Apply optional Salesforce Task/Event activity enrichment to the open FY27 Territory Plan app. The JSON must contain accounts keyed by Salesforce ID and explicit status, inbound, outbound, meetings, lastActivity, twoWay, score, tier, and reason fields.",
        parameters: { type: "object", properties: { activityPath: { type: "string", description: "Absolute path to a JSON activity enrichment file." }, instanceId: { type: "string", description: "FY27 Territory Plan canvas instance to enrich.", default: "fy27-territory-plan" } }, required: ["activityPath"] },
        handler: async ({ activityPath, instanceId = "fy27-territory-plan" }) => {
            const entry = servers.get(instanceId);
            if (!entry) throw new Error("Open the FY27 Territory Plan app before applying activity enrichment.");
            const body = await new Promise((resolve, reject) => readFile(activityPath, "utf8", (e, d) => e ? reject(e) : resolve(d)));
            const response = await fetch(`${entry.url}api/activity`, { method: "POST", headers: { "Content-Type": "application/json" }, body });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || "Salesforce activity enrichment failed.");
            return `Applied Salesforce activity enrichment to ${data.activity.enrichedAccounts} accounts; ${data.activity.twoWayAccounts} have two-way communication.`;
        },
    }],
    canvases: [createCanvas({
        id: "fy27-territory-plan", displayName: "FY27 Territory Plan", description: "Upload-first FY27 Territory Plan app with normalization stats, Innovate/Trust/Scale plays, drill-downs, and Excel export.",
        inputSchema: { type: "object", properties: { reportPath: { type: "string" }, inputPath: { type: "string" } } },
        actions: [{ name: "refresh", description: "Refresh the app from its current report.", inputSchema: { type: "object", properties: {} }, handler: async () => ({ ok: true }) }],
        open: async ctx => {
            let entry = servers.get(ctx.instanceId);
            if (!entry) { entry = await startServer(ctx.instanceId, ctx.input?.reportPath || null, ctx.input?.inputPath); servers.set(ctx.instanceId, entry); }
            return { title: "FY27 Territory Plan", url: entry.url };
        },
        onClose: async ctx => { const entry = servers.get(ctx.instanceId); if (entry) { servers.delete(ctx.instanceId); await new Promise(resolve => entry.server.close(resolve)); } },
    })],
});
