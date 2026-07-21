/** The outbox. Making the queue visible is a deliberate trust decision: a
 *  user who submitted a report during an emergency and got silence deserves
 *  to see it's saved and still trying, not wonder whether it vanished. */
import { useCallback, useEffect, useState } from "react";
import { StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { AppContext, API_BASE } from "../lib/client";
import { QueueItem } from "../lib/queue";
import { syncOnce } from "../lib/sync";

const STATUS_LABEL: Record<QueueItem["status"], string> = {
  pending: "waiting",
  sent: "sent",
  failed: "needs attention",
};

export function QueueScreen({ ctx, onChanged }: { ctx: AppContext; onChanged: () => void }) {
  const [items, setItems] = useState<QueueItem[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    // Sent items are shown briefly too (until purged on the next sync), so a
    // user gets confirmation rather than watching a row silently disappear.
    setItems(await ctx.queue.all());
  }, [ctx]);

  useEffect(() => {
    void refresh();
    const handle = setInterval(refresh, 5_000);
    return () => clearInterval(handle);
  }, [refresh]);

  async function syncNow() {
    setBusy(true);
    try {
      await syncOnce({ apiBase: API_BASE, queue: ctx.queue, clientId: ctx.deviceId });
      await ctx.queue.purgeSent();
      await refresh();
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function retryAll() {
    await ctx.queue.retryFailed();
    await refresh();
    onChanged();
  }

  return (
    <View>
      <TouchableOpacity style={[styles.button, busy && styles.busy]} onPress={syncNow} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? "Syncing…" : "Sync now"}</Text>
      </TouchableOpacity>

      {items.length === 0 && <Text style={styles.empty}>Nothing waiting. Everything has been sent.</Text>}

      {items.map((item) => (
        <View key={item.id} style={styles.row}>
          <View style={{ flex: 1 }}>
            <Text style={styles.rowTitle}>
              {item.kind === "report" ? item.payload.hazard_type ?? "report" : `check-in: ${item.payload.status}`}
            </Text>
            <Text style={styles.rowMeta}>
              {new Date(item.observedAt).toLocaleString()} · {STATUS_LABEL[item.status]}
              {item.attempts > 0 ? ` · ${item.attempts} attempt(s)` : ""}
            </Text>
            {item.lastError && <Text style={styles.rowError}>{item.lastError}</Text>}
          </View>
        </View>
      ))}

      {items.some((i) => i.status === "failed") && (
        <TouchableOpacity style={styles.secondary} onPress={retryAll}>
          <Text style={styles.secondaryText}>Retry failed items</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  button: { backgroundColor: "#415a77", borderRadius: 8, padding: 14, alignItems: "center" },
  busy: { opacity: 0.6 },
  buttonText: { color: "#e0e1dd", fontWeight: "700", fontSize: 15 },
  empty: { color: "#5c677d", marginTop: 24, fontSize: 13 },
  row: {
    flexDirection: "row", backgroundColor: "#1b263b", borderRadius: 8,
    padding: 12, marginTop: 12,
  },
  rowTitle: { color: "#e0e1dd", fontSize: 15, fontWeight: "600" },
  rowMeta: { color: "#8d99ae", fontSize: 12, marginTop: 2 },
  rowError: { color: "#f4a261", fontSize: 12, marginTop: 4 },
  secondary: { marginTop: 16, padding: 12, alignItems: "center" },
  secondaryText: { color: "#8d99ae", fontSize: 14 },
});
