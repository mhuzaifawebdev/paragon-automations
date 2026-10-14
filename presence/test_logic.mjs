// Offline tests for presence/logic.js. Run: node presence/test_logic.mjs
import { decide, inShift, minutesBetween } from "./logic.js";

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  ok ? pass++ : fail++;
  console.log((ok ? "ok   " : "FAIL ") + name + (ok ? "" : `: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`));
};
const at = s => new Date(`2026-10-14T${s}:00Z`);
const on = { online: true, day: "2026-10-14", since: "2026-10-14T07:00:00.000Z", row: null };

check("shift window", [inShift(at("06:59"), "07:00", "16:00"), inShift(at("07:00"), "07:00", "16:00"), inShift(at("16:01"), "07:00", "16:00")], [false, true, false]);
check("first check ever: status remembered, nothing written to the sheet", decide(null, false, at("09:00"), true).action, "none");
check("first check of a new day: someone already offline is not a log-out",
      decide({ ...on, day: "2026-10-13" }, false, at("07:00"), true).action, "none");
check("online -> offline in the shift is a log-out", decide(on, false, at("10:02"), true).action, "logout");
check("online -> offline after the shift is not", decide(on, false, at("16:30"), false).action, "none");
check("no answer from Zadarma: nothing happens and nothing is saved", decide(on, null, at("10:02"), true), { action: "none", next: null });
check("no change: nothing is saved (keeps the free storage allowance)", decide(on, true, at("10:02"), true).next, null);
const away = { online: false, day: "2026-10-14", since: "2026-10-14T10:02:00.000Z", row: 7 };
const back = decide(away, true, at("10:31"), true);
check("offline -> online with an open log-out closes it", back.action, "login");
check("and the open row is cleared", back.next.row, null);
check("minutes away are counted from the log-out", minutesBetween(away.since, at("10:31")), 29);
check("coming back late, after the shift, still closes the row", decide(away, true, at("16:40"), false).action, "login");
check("offline -> online without an open log-out writes nothing", decide({ ...away, row: null }, true, at("10:31"), true).action, "none");
check("away overnight: the open row is kept into the next day",
      decide({ ...away, day: "2026-10-13" }, false, at("07:00"), true).next.row, 7);
check("and closed when they log back in the next day", decide({ ...away, day: "2026-10-13" }, true, at("07:05"), true).action, "login");

console.log(`\n${pass}/${pass + fail} passed`);
process.exit(fail ? 1 : 0);
