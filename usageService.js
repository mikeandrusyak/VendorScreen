const { Storage } = require('@mondaycom/apps-sdk');

// Soft cap on paid OpenSanctions calls per account per calendar month. This is
// NOT tied to Monday's native billing yet (see TECH_SPEC.md §7) — it exists to
// protect us from an unbounded API bill while a customer is on a free/basic
// plan. Raise via env until per-plan limits are wired to Monday subscriptions.
const MONTHLY_LIMIT = parseInt(process.env.MONTHLY_CHECK_LIMIT || '100', 10);

function currentPeriodKey() {
  const now = new Date();
  return `usage_${now.getUTCFullYear()}_${String(now.getUTCMonth() + 1).padStart(2, '0')}`;
}

// Attempts to consume one usage slot for the account owning `apiToken`.
// Storage is scoped per-account by Monday (keyed off the token), so each
// customer gets their own independent counter. Uses the stored version as an
// optimistic lock to avoid two concurrent requests both reading the same
// count and under-counting.
async function reserveUsageSlot(apiToken, attempts = 3) {
  const storage = new Storage(apiToken);
  const key = currentPeriodKey();

  for (let attempt = 1; attempt <= attempts; attempt++) {
    let current;
    try {
      current = await storage.get(key);
    } catch (err) {
      console.warn('[usage] Storage read failed — allowing check through:', err.message);
      return { allowed: true, count: null, limit: MONTHLY_LIMIT };
    }

    const count = typeof current.value === 'number' ? current.value : 0;

    if (count >= MONTHLY_LIMIT) {
      return { allowed: false, count, limit: MONTHLY_LIMIT };
    }

    try {
      const result = await storage.set(key, count + 1, {
        previousVersion: current.version,
      });
      if (result.success) {
        return { allowed: true, count: count + 1, limit: MONTHLY_LIMIT };
      }
      // Version conflict (another concurrent request won) — retry with a fresh read.
      console.warn(`[usage] Counter write conflict on attempt ${attempt}/${attempts}, retrying`);
    } catch (err) {
      console.warn('[usage] Storage write failed — allowing check through:', err.message);
      return { allowed: true, count: null, limit: MONTHLY_LIMIT };
    }
  }

  console.warn('[usage] Exhausted retries on counter write — allowing check through');
  return { allowed: true, count: null, limit: MONTHLY_LIMIT };
}

module.exports = { reserveUsageSlot, MONTHLY_LIMIT };
