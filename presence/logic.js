/**
 * The decision part of the presence watcher, kept free of network calls so it can be tested offline
 * (node presence/test_logic.mjs). worker.js does the Zadarma, KV and Google Sheets calls around it.
 *
 * State kept per extension (Cloudflare KV, written only when it changes):
 *   { online: true|false, day: "YYYY-MM-DD", since: ISO time of the last change, row: sheet row of an open log-out or null }
 */

export function inShift(now, startUtc, endUtc) {
  const hm = now.toISOString().slice(11, 16);
  return startUtc <= hm && hm <= endUtc;
}

/**
 * prev: saved state or null. online: true / false / null (Zadarma did not say).
 * Returns { action: "none" | "logout" | "login", next: state to save, or null when nothing changed }.
 *  - Zadarma did not answer: nothing happens, so a hiccup is never recorded as a log-out.
 *  - First check of the day: the status is only remembered. Someone already offline when the shift
 *    starts did not log out during the shift (the pause monitor reports that case).
 *  - online -> offline during the shift: a log-out starts.
 *  - offline -> online with an open log-out: it ends, whatever the time.
 */
export function decide(prev, online, now, shift) {
  if (online !== true && online !== false) return { action: "none", next: null };
  const day = now.toISOString().slice(0, 10);
  const iso = now.toISOString();
  if (!prev || prev.day !== day) {
    if (prev && prev.row) {
      if (online === true) return { action: "login", next: { online, day, since: iso, row: null } };
      return { action: "none", next: { ...prev, day } };          // still away: keep the open row
    }
    return { action: "none", next: { online, day, since: iso, row: null } };
  }
  if (prev.online === online) return { action: "none", next: null };
  if (prev.online === true && online === false) {
    return { action: shift ? "logout" : "none", next: { online, day, since: iso, row: null } };
  }
  return { action: prev.row ? "login" : "none", next: { online, day, since: iso, row: null } };
}

export function minutesBetween(fromIso, toDate) {
  return Math.max(0, Math.round((toDate.getTime() - Date.parse(fromIso)) / 60000));
}

// Zadarma signs with the md5 of the query string. Every call here has no parameters, so that is md5 of "".
export const MD5_OF_EMPTY = "d41d8cd98f00b204e9800998ecf8427e";
