/**
 * OceanPing mobile (phase 3, milestone 5).
 *
 * Three screens, all of which work with no network: reporting a hazard,
 * marking yourself safe, and seeing what's still waiting to sync. Everything
 * a user submits goes into the durable queue first (see src/lib/queue.ts) and
 * uploads whenever a network next appears.
 */
import { useCallback, useEffect, useState } from "react";
import { SafeAreaView, ScrollView, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import * as SQLite from "expo-sqlite";
import { StatusBar } from "expo-status-bar";

import { AppContext, initApp, startSyncLoop } from "./src/lib/client";
import { QueueScreen } from "./src/screens/QueueScreen";
import { ReportScreen } from "./src/screens/ReportScreen";
import { SafetyScreen } from "./src/screens/SafetyScreen";

type Tab = "report" | "safe" | "queue";

const TABS: { key: Tab; label: string }[] = [
  { key: "report", label: "Report" },
  { key: "safe", label: "Mark Safe" },
  { key: "queue", label: "Outbox" },
];

export default function App() {
  const [ctx, setCtx] = useState<AppContext | null>(null);
  const [tab, setTab] = useState<Tab>("report");
  const [pending, setPending] = useState(0);

  useEffect(() => {
    let stopSync: (() => void) | undefined;
    (async () => {
      const app = await initApp(async () => SQLite.openDatabaseAsync("oceanping.db") as any);
      setCtx(app);
      stopSync = startSyncLoop(app);
    })();
    return () => stopSync?.();
  }, []);

  const refreshPending = useCallback(async () => {
    if (!ctx) return;
    setPending((await ctx.queue.pending()).length);
  }, [ctx]);

  useEffect(() => {
    if (!ctx) return;
    void refreshPending();
    const handle = setInterval(refreshPending, 5_000);
    return () => clearInterval(handle);
  }, [ctx, refreshPending]);

  if (!ctx) {
    return (
      <SafeAreaView style={styles.container}>
        <Text style={styles.loading}>Starting OceanPing…</Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <StatusBar style="light" />
      <View style={styles.header}>
        <Text style={styles.title}>OceanPing</Text>
        <Text style={styles.subtitle}>
          {pending > 0 ? `${pending} waiting to sync` : "All reports synced"}
        </Text>
      </View>

      {ctx.degraded && (
        <Text style={styles.warning}>
          Local storage unavailable — queued items will be lost if the app closes.
        </Text>
      )}

      <View style={styles.tabs}>
        {TABS.map((t) => (
          <TouchableOpacity
            key={t.key}
            style={[styles.tab, tab === t.key && styles.tabActive]}
            onPress={() => setTab(t.key)}
          >
            <Text style={[styles.tabText, tab === t.key && styles.tabTextActive]}>{t.label}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView contentContainerStyle={styles.body}>
        {tab === "report" && <ReportScreen ctx={ctx} onSubmitted={refreshPending} />}
        {tab === "safe" && <SafetyScreen ctx={ctx} onSubmitted={refreshPending} />}
        {tab === "queue" && <QueueScreen ctx={ctx} onChanged={refreshPending} />}
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#0d1b2a" },
  loading: { color: "#e0e1dd", padding: 24, fontSize: 16 },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8 },
  title: { color: "#e0e1dd", fontSize: 24, fontWeight: "700" },
  subtitle: { color: "#8d99ae", fontSize: 13, marginTop: 2 },
  warning: {
    color: "#0d1b2a", backgroundColor: "#f4a261", marginHorizontal: 16,
    padding: 8, borderRadius: 6, fontSize: 12,
  },
  tabs: { flexDirection: "row", paddingHorizontal: 16, gap: 8, marginTop: 8 },
  tab: { paddingVertical: 8, paddingHorizontal: 14, borderRadius: 999, backgroundColor: "#1b263b" },
  tabActive: { backgroundColor: "#415a77" },
  tabText: { color: "#8d99ae", fontSize: 14 },
  tabTextActive: { color: "#e0e1dd", fontWeight: "600" },
  body: { padding: 16, paddingBottom: 48 },
});
