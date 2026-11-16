"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const pluginPath = path.resolve(__dirname, "../integrations/siyuan/index.js");
function load(remote) {
    const wrapper = new Function("require", "module", fs.readFileSync(pluginPath, "utf8"));
    const mod = { exports: {} };
    wrapper(name => {
        if (name === "siyuan") return {
            showMessage() {},
            Plugin: class { addTopBar(registration) { this.topBar = registration; } addAgentCapability(registration) { this.capability = registration; } },
        };
        if (name === "@electron/remote") return remote;
        return require(name);
    }, mod);
    return mod.exports;
}

test("use the native Agent tool and require a visible confirmation before any write", async () => {
    let calls = 0;
    let confirmation;
    const remote = {
        BrowserWindow: class {},
        session: { fromPartition: () => ({
            async fetch() {
                calls++;
                return { status: 200, text: async () => '{"result_code":200}' };
            },
        }) },
        getGlobal: () => AbortSignal,
        getCurrentWindow: () => ({}),
        dialog: { showMessageBox: async (_, opts) => { confirmation = opts; return { response: 0 }; } },
    };
    const plugin = new (load(remote))();
    plugin.onload();
    assert.equal(plugin.capability.name, "bbdc_wordbook");
    assert.match(plugin.topBar.icon, /^<svg/);
    assert.match(plugin.topBar.icon, /currentColor/);
    assert.equal(plugin.topBar.title, "不背单词 · 登录");
    const preview = await plugin.capability.handler({ action: "preview", words: [" Resilient", "resilient", "coach’s"] });
    assert.deepEqual(preview.structuredContent.terms, ["resilient", "coach's"]);
    assert.equal(calls, 0);
    assert.match((await plugin.capability.handler({ action: "push", words: ["bad!"] })).error, /第 1 项/);
    assert.equal(calls, 0);
    const cancelled = await plugin.capability.handler({ action: "push", words: ["Resilient"] });
    assert.equal(cancelled.structuredContent.status, "cancelled");
    assert.equal(confirmation.detail, "resilient");
    assert.equal(confirmation.defaultId, 0);
    assert.equal(calls, 1); // Only check-login, no word lookup or write.
});

test("check-only does not request definitions or add; push reports a lost write separately", async () => {
    const { createClient, execute } = load().test;
    const actions = [];
    const client = createClient(async (raw, options) => {
        const url = new URL(raw);
        const word = url.searchParams.get("word");
        actions.push([url.pathname, word, options]);
        if (url.pathname === "/api/check-login") return { status: 200, data: { result_code: 200 } };
        if (url.pathname === "/api/check-new-word") {
            return { status: 200, data: { result_code: 200, data_body: { list: word === "already" ? [word] : [] } } };
        }
        if (url.pathname === "/loadLexisList.action") {
            assert.equal(options.account, false);
            return { status: 200, data: { wordlist: [] } };
        }
        if (url.pathname === "/api/user-new-word") {
            assert.deepEqual(options.body, {
                word: options.body.word, info: "", course: "*", wordidx: "*", infoidx: "100", selection: "*", opcode: "1",
            });
            if (options.body.word === "unclear") return { status: 502 };
            return { status: 200, data: { result_code: 200 } };
        }
        assert.fail(url.pathname);
    });
    const check = await execute(client, "check", ["already", "not yet"], () => assert.fail("no confirmation"), { gapMs: 0 });
    assert.deepEqual(check.existing, ["already"]);
    assert.deepEqual(check.missing, ["not yet"]);
    assert.equal(actions.length, 3);
    actions.length = 0;
    const push = await execute(client, "push", ["already", "fresh", "unclear", "later"], async () => true, { gapMs: 0 });
    assert.equal(push.status, "write_unconfirmed");
    assert.deepEqual(push.added, ["fresh"]);
    assert.deepEqual(push.without_definition, ["fresh"]);
    assert.deepEqual(push.uncertain, ["unclear"]);
    assert.deepEqual(push.remaining, ["later"]);
    assert.equal(actions.filter(a => a[0] === "/api/user-new-word").length, 2);
});

test("HTTP 200 logged-out response and HTTP 429 stop the batch before further requests", async () => {
    const { createClient, execute } = load().test;
    for (const [response, status] of [
        [{ status: 200, data: { result_code: 20000, error_body: { user_message: "请登录" } } }, "login_required"],
        [{ status: 429 }, "rate_limited"],
    ]) {
        let calls = 0;
        const client = createClient(async () => { calls++; return response; });
        const result = await execute(client, "push", ["fresh"], () => assert.fail("not ready"));
        assert.equal(result.status, status);
        assert.deepEqual(result.remaining, ["fresh"]);
        assert.equal(calls, 1);
    }
});
