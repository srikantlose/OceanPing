/** Hazard report form. Submitting only ever writes to the local queue — the
 *  upload happens later, so the button never blocks on a network. */
import { useState } from "react";
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";

import { AppContext } from "../lib/client";

const HAZARDS = [
  ["coastal_flooding", "Coastal flooding"],
  ["storm_surge", "Storm surge"],
  ["high_waves", "High waves"],
  ["tsunami", "Tsunami signs"],
  ["rip_current", "Rip current"],
  ["oil_spill", "Oil spill"],
  ["algal_bloom", "Algal bloom"],
  ["erosion", "Coastal erosion"],
  ["other", "Other"],
] as const;

export function ReportScreen({ ctx, onSubmitted }: { ctx: AppContext; onSubmitted: () => void }) {
  const [hazard, setHazard] = useState<string>("coastal_flooding");
  const [text, setText] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setStatus(null);
    try {
      // Location is requested at submit time rather than held continuously —
      // a hazard reporter shouldn't be tracked in the background.
      const perm = await Location.requestForegroundPermissionsAsync();
      if (!perm.granted) {
        setStatus("Location permission is needed to place the report.");
        return;
      }
      const pos = await Location.getCurrentPositionAsync({});
      await ctx.queue.enqueue({
        kind: "report",
        payload: {
          lat: pos.coords.latitude.toFixed(6),
          lon: pos.coords.longitude.toFixed(6),
          hazard_type: hazard,
          ...(text ? { text } : {}),
        },
      });
      setText("");
      setStatus("Saved. It will upload as soon as there's a network.");
      onSubmitted();
    } catch (err: any) {
      setStatus(`Could not save: ${err?.message ?? err}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <View>
      <Text style={styles.label}>What do you see?</Text>
      <View style={styles.chips}>
        {HAZARDS.map(([value, label]) => (
          <TouchableOpacity
            key={value}
            style={[styles.chip, hazard === value && styles.chipActive]}
            onPress={() => setHazard(value)}
          >
            <Text style={[styles.chipText, hazard === value && styles.chipTextActive]}>{label}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <Text style={styles.label}>Describe it (any language, optional)</Text>
      <TextInput
        style={styles.input}
        value={text}
        onChangeText={setText}
        placeholder="What is happening?"
        placeholderTextColor="#5c677d"
        multiline
      />

      <TouchableOpacity style={[styles.button, busy && styles.buttonBusy]} onPress={submit} disabled={busy}>
        <Text style={styles.buttonText}>{busy ? "Saving…" : "Submit report"}</Text>
      </TouchableOpacity>

      {status && <Text style={styles.status}>{status}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  label: { color: "#8d99ae", fontSize: 13, marginTop: 16, marginBottom: 8 },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 999, backgroundColor: "#1b263b" },
  chipActive: { backgroundColor: "#415a77" },
  chipText: { color: "#8d99ae", fontSize: 13 },
  chipTextActive: { color: "#e0e1dd", fontWeight: "600" },
  input: {
    backgroundColor: "#1b263b", color: "#e0e1dd", borderRadius: 8,
    padding: 12, minHeight: 90, textAlignVertical: "top",
  },
  button: { backgroundColor: "#e63946", borderRadius: 8, padding: 14, alignItems: "center", marginTop: 20 },
  buttonBusy: { opacity: 0.6 },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 16 },
  status: { color: "#8d99ae", marginTop: 12, fontSize: 13 },
});
