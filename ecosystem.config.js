// PM2 process definition for TicketsPlease.
//
//   pm2 start ecosystem.config.js     # launch (or reload) the tracker
//   pm2 logs tickets-please           # tail its output
//   pm2 stop tickets-please           # stop it
//   pm2 save                          # remember it across reboots (see below)
//
// To have PM2 relaunch this on boot, run `pm2 startup` once (it prints a
// command to paste), then `pm2 save` after the app is up. The old start.sh /
// start.bat launchers still work for a quick one-off run.
module.exports = {
  apps: [
    {
      name: "tickets-please",
      script: "app.py",
      interpreter: "python3",
      cwd: __dirname,

      // Single-user, single-port, SQLite-backed: exactly one instance, and
      // never cluster mode (that would fork multiple listeners on 5137).
      instances: 1,
      exec_mode: "fork",

      env: {
        // Flush Python's stdout/stderr straight through to PM2's logs.
        PYTHONUNBUFFERED: "1",
        // Expose on the LAN (http://<this-host>:5137). No auth — trusted
        // networks only. Set to 127.0.0.1 to keep it local to this machine.
        HOST: "0.0.0.0",
        PORT: "5137",
      },

      // Keep it alive, but stop hammering if it can't stay up.
      autorestart: true,
      max_restarts: 10,
      min_uptime: "10s",
      restart_delay: 2000,

      // Logs live next to the app (see .gitignore), merged and timestamped.
      merge_logs: true,
      time: true,
      out_file: "logs/tickets-please.out.log",
      error_file: "logs/tickets-please.err.log",
    },
  ],
};
