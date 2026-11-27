import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { createWorkspaceHome } from "./workspace-fixture.mjs";
import { readJson, removeDir, tempDir } from "./helpers.mjs";

test("preview pins both models and classic HTML beneath an empty synthetic destination", t => {
    const home = tempDir();
    t.after(() => removeDir(home));
    createWorkspaceHome(home);
    const config = readJson(path.join(home, "config.json"));
    const model = readJson(path.join(home, "model.json"));
    assert.equal(config.copilot_home, path.join(home, "synthetic-copilot"));
    assert.equal(model.copilot.model_path, path.join(config.copilot_home, "model.json"));
    assert.equal(readJson(model.copilot.model_path).accounts.length, 64);
    assert.match(fs.readFileSync(path.join(config.copilot_home, "dashboard.html"), "utf8"), /Synthetic classic preview/);
});

test("preview launcher refuses a nonempty synthetic destination before changing any sentinel", t => {
    const home = tempDir();
    t.after(() => removeDir(home));
    const sentinels = ["model.json", "config.json", "status.json"];
    for (const file of sentinels) fs.writeFileSync(path.join(home, file), `Synthetic sentinel: ${file}`);
    const result = spawnSync(process.execPath, [fileURLToPath(new URL("./workspace-preview.mjs", import.meta.url)), home],
        { encoding: "utf8", timeout: 5000 });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Refusing preview destination/);
    assert.deepEqual(fs.readdirSync(home).sort(), sentinels.sort());
    for (const file of sentinels) assert.equal(fs.readFileSync(path.join(home, file), "utf8"), `Synthetic sentinel: ${file}`);
});

test("preview refuses a symbolic-link destination even when its target is empty", t => {
    const home = tempDir();
    t.after(() => removeDir(home));
    const target = path.join(home, "target"), link = path.join(home, "link");
    fs.mkdirSync(target);
    fs.symlinkSync(target, link, "dir");
    assert.throws(() => createWorkspaceHome(link), /Refusing preview destination/);
    assert.deepEqual(fs.readdirSync(target), []);
});
