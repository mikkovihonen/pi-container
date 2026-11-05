// Preload shim for T3 Code server inside pi-container.
//
// Intercepts network interfaces so T3 Code advertises localhost or the host's IP
// instead of the container's internal network IP (e.g. 10.89.0.x) in pairing
// URLs, connection strings, app links, and the terminal QR code.
const os = require('node:os');

const origInterfaces = os.networkInterfaces.bind(os);
const targetHost = (process.env.T3_ADVERTISE_HOST || 'localhost').trim();

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

    // When an explicit targetHost is configured (e.g. host LAN IP or custom domain):
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

