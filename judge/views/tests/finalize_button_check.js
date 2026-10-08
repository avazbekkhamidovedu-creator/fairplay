// Execute the actual rendered click handler with controlled AJAX responses and time.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const script = require('node:fs').readFileSync(0, 'utf8');
const [expectedPostUrl, expectedStatusUrl] = process.argv.slice(2);

function setup() {
    const timers = [], requests = [], toasts = [];
    let click, confirmed = true, reloads = 0;
    const button = {
        disabled: false,
        label: 'initial',
        on(event, callback) { click = callback; return this; },
        prop(name, value) { this[name] = value; return this; },
        text(value) { this.label = value; return this; }
    };
    function request(url, data) {
        const callbacks = {};
        const deferred = {};
        for (const name of ['done', 'fail', 'always']) {
            deferred[name] = callback => { callbacks[name] = callback; return deferred; };
        }
        requests.push({url, data,
            resolve(value) { callbacks.done(value); if (callbacks.always) callbacks.always(); },
            reject(value = {}) { callbacks.fail(value); if (callbacks.always) callbacks.always(); }
        });
        return deferred;
    }
    const $ = () => button;
    $.post = url => {
        assert.equal(url, expectedPostUrl);
        return request(url);
    };
    $.ajax = options => {
        assert.equal(options.url, expectedStatusUrl);
        assert.equal(options.timeout, 10000);
        return request(options.url, options.data);
    };
    const location = {href: '', reload() { reloads++; }};
    vm.runInNewContext(script, {
        $, confirm: () => confirmed,
        setTimeout: (callback, delay) => timers.push({callback, delay}),
        window: {location, showToast: (...args) => toasts.push(args)}
    });
    return {button, requests, timers, location, toasts,
        click() { if (!button.disabled) click.call(button); },
        cancel() { confirmed = false; },
        tick() { assert.ok(timers.length); timers.shift().callback(); },
        reloads() { return reloads; },
        queue() {
            this.click();
            assert.equal(button.disabled, true);
            requests.shift().resolve({ok: true, task_id: 'task-123', status_url: '/progress'});
            assert.equal(button.disabled, true);
            assert.equal(button.label, 'Queued');
            assert.equal(timers[0].delay, 0);
            this.click();
            assert.equal(requests.length, 0, 'disabled button must not resubmit');
            this.tick();
            assert.equal(requests[0].data.id, 'task-123');
        }
    };
}

let ui = setup();
ui.cancel();
ui.click();
assert.equal(ui.requests.length, 0);
assert.equal(ui.button.disabled, false);

for (const networkFailure of [false, true]) {
    ui = setup();
    ui.click();
    if (networkFailure) ui.requests.shift().reject();
    else ui.requests.shift().resolve({ok: false, error: 'denied'});
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.toasts[0][1], 'error');
    ui.click();
    assert.equal(ui.requests.length, 1);
}

ui = setup();
ui.queue();
ui.requests.shift().resolve({code: 'WORKING'});
assert.equal(ui.reloads(), 0);
assert.equal(ui.timers[0].delay, 2000);
ui.tick();
ui.requests.shift().resolve({code: 'PROGRESS'});
assert.equal(ui.button.label, 'Processing');
assert.equal(ui.button.disabled, true);
ui.tick();
ui.requests.shift().resolve({code: 'SUCCESS'});
assert.equal(ui.reloads(), 1);
assert.equal(ui.timers.length, 0);

ui = setup();
ui.queue();
ui.requests.shift().resolve({code: 'SUCCESS'});
assert.equal(ui.reloads(), 1, 'an immediately finished task must also refresh');
assert.equal(ui.timers.length, 0);

// Intermittent network errors must recover, resetting the consecutive-failure count.
ui = setup();
ui.queue();
for (let round = 0; round < 2; round++) {
    for (let i = 0; i < 4; i++) {
        ui.requests.shift().reject();
        assert.equal(ui.button.disabled, true);
        ui.tick();
    }
    ui.requests.shift().resolve({code: 'WORKING'});
    ui.tick();
}
ui.requests.shift().resolve({code: 'SUCCESS'});
assert.equal(ui.reloads(), 1);
assert.equal(ui.timers.length, 0);

ui = setup();
ui.queue();
ui.requests.shift().resolve({code: 'FAILURE'});
assert.equal(ui.button.label, 'Refresh');
assert.equal(ui.button.disabled, false);
assert.equal(ui.timers.length, 0);
ui.click();
assert.equal(ui.reloads(), 1);
assert.equal(ui.requests.length, 0);

for (const networkFailure of [false, true]) {
    ui = setup();
    ui.queue();
    const attempts = networkFailure ? 5 : 120;
    for (let i = 0; i < attempts; i++) {
        if (networkFailure) ui.requests.shift().reject();
        else ui.requests.shift().resolve({code: 'WORKING'});
        if (i < attempts - 1) ui.tick();
    }
    assert.equal(ui.button.label, 'Check progress');
    assert.equal(ui.button.disabled, false);
    assert.equal(ui.timers.length, 0);
    ui.click();
    assert.equal(ui.location.href, '/progress');
    assert.equal(ui.requests.length, 0);
}
