// Preload shim for T3 Code server inside pi-container.
//
// Intercepts network interfaces and public URLs so T3 Code advertises localhost,
// custom domains, or secure tunnel URLs in pairing links, connection strings,
// app links, and terminal QR codes.
const os = require('node:os');
const Module = require('node:module');

function sanitizePublicUrl(raw) {
    if (!raw) return '';
    try {
        const u = new URL(raw.startsWith('http://') || raw.startsWith('https://') ? raw : `https://${raw}`);
        const host = (u.hostname.includes(':') && !u.hostname.startsWith('[')) ? `[${u.hostname}]` : u.hostname;
        return `https://${host}`;
    } catch {
        return raw.replace(/^http:\/\//, 'https://').replace(/:\d+/, '').replace(/\/+$/, '');
    }
}

const publicUrl = sanitizePublicUrl(process.env.T3_PUBLIC_URL);
let targetHost = (process.env.T3_ADVERTISE_HOST || '').trim();

if (!targetHost && publicUrl) {
    try {
        const parsed = new URL(publicUrl);
        targetHost = parsed.hostname;
    } catch {}
}
if (!targetHost) {
    targetHost = 'localhost';
}

const origInterfaces = os.networkInterfaces.bind(os);

os.networkInterfaces = function () {
    const interfaces = origInterfaces();
    if (!targetHost || targetHost === 'localhost' || targetHost === '127.0.0.1') {
        // Omitting non-loopback interfaces causes T3 Code's resolver to fall back to 'localhost'
        return {
            lo: interfaces.lo || [{
                address: '127.0.0.1',
                netmask: '255.0.0.0',
                family: 'IPv4',
                mac: '00:00:00:00:00:00',
                internal: true,
                cidr: '127.0.0.1/8'
            }]
        };
    }

    // When an explicit targetHost is configured (e.g. host LAN IP, custom domain, or tunnel hostname):
    return {
        adv0: [{
            address: targetHost,
            netmask: '255.255.255.0',
            family: targetHost.includes(':') ? 'IPv6' : 'IPv4',
            mac: '00:00:00:00:00:00',
            internal: false,
            cidr: `${targetHost}/24`
        }],
        ...interfaces
    };
};

if (publicUrl) {
    function rewriteString(str) {
        if (typeof str !== 'string' || !publicUrl) return str;
        if (str.includes('/pair#token=')) {
            str = str.replace(/https?:\/\/[^\/:]+(?::\d+)?\/pair#token=([a-zA-Z0-9_\.-]+)/g, `${publicUrl}/pair#token=$1`);
        }
        if (str.includes('Connection string:')) {
            str = str.replace(/Connection string:\s*https?:\/\/[^\s]+/g, `Connection string: ${publicUrl}`);
        }
        if (str.includes('App link:')) {
            str = str.replace(/pairingUrl=https?%3A%2F%2F[^&%\s]+(?::\d+)?(%2Fpair%23token%3D[a-zA-Z0-9_\.-]+)/g, (m, suffix) => {
                return `pairingUrl=${encodeURIComponent(publicUrl)}${suffix}`;
            });
        }
        if (str.includes('Listening on http')) {
            str = str.replace(/Listening on http:\/\/[^\s]+/g, `Listening on ${publicUrl} (tunnel terminating to container port 3773)`);
        }
        return str;
    }

    // Hook URL.prototype.toString so T3 Code's internal buildPairingUrl returns the canonical zero-port HTTPS URL.
    // This ensures T3 Code's internal Nayuki QR code generator renders the QR code with public HTTPS without port,
    // and buildPairingAppLink generates the proper encoded t3code:// URL.
    const origUrlToString = URL.prototype.toString;
    URL.prototype.toString = function () {
        if (this.pathname === '/pair' && this.hash && this.hash.includes('token=')) {
            return `${publicUrl}/pair${this.hash}`;
        }
        return origUrlToString.call(this);
    };

    // Intercept console.log to replace connection strings, pairing links, app links, and listener banners
    const origLog = console.log.bind(console);
    console.log = function (...args) {
        for (let i = 0; i < args.length; i++) {
            if (typeof args[i] === 'string') {
                args[i] = rewriteString(args[i]);
            }
        }
        return origLog.apply(console, args);
    };

    // Intercept process.stdout.write for raw streams
    const origStdoutWrite = process.stdout.write.bind(process.stdout);
    process.stdout.write = function (chunk, encoding, callback) {
        if (typeof chunk === 'string') {
            chunk = rewriteString(chunk);
        }
        return origStdoutWrite(chunk, encoding, callback);
    };

    // Intercept qrcode-terminal to render QR code for the public HTTPS tunnel URL if used externally
    const origRequire = Module.prototype.require;
    Module.prototype.require = function (id) {
        const res = origRequire.apply(this, arguments);
        if (id === 'qrcode-terminal' || (typeof id === 'string' && id.endsWith('/qrcode-terminal'))) {
            if (res && typeof res.generate === 'function') {
                const origGen = res.generate.bind(res);
                res.generate = function (input, opts, cb) {
                    if (typeof input === 'string' && input.includes('/pair#token=')) {
                        const tokenMatch = input.match(/[#&]token=([a-zA-Z0-9_\.-]+)/);
                        if (tokenMatch) {
                            input = `${publicUrl}/pair#token=${tokenMatch[1]}`;
                        }
                    }
                    return origGen.call(res, input, opts, cb);
                };
            }
        }
        return res;
    };
}
