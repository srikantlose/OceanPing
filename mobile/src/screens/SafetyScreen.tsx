/** "Mark Safe" — two buttons, because during an evacuation the interaction
 *  budget is roughly one tap. Queued locally like everything else. */
import { useState } from "react";
import { StyleSheet, Text, TextInput, TouchableOpacity, View } from "react-native";
import * as Location from "expo-location";

import { AppContext } from "../lib/client";

export function SafetyScreen({ ctx, onSubmitted }: { ctx: AppContext; onSubmitted: () => void }) {
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function checkIn(state: "safe" | "need_help") {
    setBusy(true);
    setStatus(null);
    try {
      const perm = await Location.requestForegroundPermissionsAsync();
      if (!perm.granted) {
        setStatus("Location permission is needed so responders know where you are.");
        return;
      }
      const pos = await Location.getCurrentPositionAsync({});
      await ctx.queue.enqueue({
        kind: "checkin",
        payload: {
          lat: pos.coords.latitude.toFixed(6),
          lon: pos.coords.longitude.toFixed(6),
          status: state,
          ...(note ? { note } : {}),
        },
      });
      setNote("");
      setStatus(
        state === "safe"
          ? "Saved as safe. It will send as soon as there's a network."
          : "Saved as needing help. It will send as soon as there's a network.",
      );
      onSubmitted();
    } catch (err: any) {
      setStatus(`Could not save: ${err?.message ?? err}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <View>
      <Text style={styles.intro}>
        Tell responders how you are. This works with no signal — it is stored on your phone and sent
        automatically once a network is available.
      </Text>

      <TouchableOpacity style={[styles.safe, busy && styles.busy]} onPress={() => checkIn("safe")} disabled={busy}>
        <Text style={styles.buttonText}>I am safe</Text>
      </TouchableOpacity>

      <TouchableOpacity style={[styles.help, busy && styles.busy]} onPress={() => checkIn("need_help")} disabled={busy}>
        <Text style={styles.buttonText}>I need help</Text>
      </TouchableOpacity>

      <Text style={styles.label}>Add a note (optional)</Text>
      <TextInput
        style={styles.input}
        value={note}
        onChangeText={setNote}
        placeholder="Anything responders should know"
        placeholderTextColor="#5c677d"
        multiline
      />

      {status && <Text style={styles.status}>{status}</Text>}

      <Text style={styles.footnote}>
        For a life-threatening emergency, call 112 if you can. This app does not replace emergency
        services.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  intro: { color: "#8d99ae", fontSize: 13, marginBottom: 20, lineHeight: 19 },
  safe: { backgroundColor: "#2a9d8f", borderRadius: 8, padding: 18, alignItems: "center" },
  help: { backgroundColor: "#e63946", borderRadius: 8, padding: 18, alignItems: "center", marginTop: 12 },
  busy: { opacity: 0.6 },
  buttonText: { color: "#fff", fontWeight: "700", fontSize: 17 },
  label: { color: "#8d99ae", fontSize: 13, marginTop: 24, marginBottom: 8 },
  input: {
    backgroundColor: "#1b263b", color: "#e0e1dd", borderRadius: 8,
    padding: 12, minHeight: 70, textAlignVertical: "top",
  },
  status: { color: "#8d99ae", marginTop: 12, fontSize: 13 },
  footnote: { color: "#5c677d", fontSize: 12, marginTop: 24, lineHeight: 17 },
});
