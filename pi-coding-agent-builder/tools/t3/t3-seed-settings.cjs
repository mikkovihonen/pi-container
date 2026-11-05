// Pre-seed T3 Code settings to enable Pi and disable other agent drivers.
const fs = require("node:fs");
const path = "/home/pi/.pi/agent/t3/userdata/settings.json";

try {
    fs.mkdirSync(require("node:path").dirname(path), { recursive: true });
    let settings = {};
    try {
        settings = JSON.parse(fs.readFileSync(path, "utf8"));
    } catch {}
    settings.providerInstances = settings.providerInstances || {};
    settings.providerInstances.pi = Object.assign(settings.providerInstances.pi || {}, { driver: "pi", enabled: true });
    settings.providerInstances.codex = Object.assign(settings.providerInstances.codex || {}, { driver: "codex", enabled: false });
    settings.providerInstances.claudeAgent = Object.assign(settings.providerInstances.claudeAgent || {}, { driver: "claudeAgent", enabled: false });
    if (
        !settings.textGenerationModelSelection ||
        !settings.textGenerationModelSelection.instanceId ||
        settings.textGenerationModelSelection.instanceId === "codex"
    ) {
        settings.textGenerationModelSelection = { instanceId: "pi", model: "default" };
    }
    fs.writeFileSync(path, JSON.stringify(settings, null, 2));
} catch (err) {
    console.error("Warning: failed to seed T3 settings:", err.message);
}
