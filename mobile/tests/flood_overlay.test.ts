import { describe, expect, it } from "vitest";
import { DevicePose, projectFloodLine } from "../src/lib/flood_overlay";

function pose(overrides: Partial<DevicePose> = {}): DevicePose {
  return { heightM: 1.5, pitchDeg: 0, verticalFovDeg: 60, ...overrides };
}

describe("projectFloodLine", () => {
  it("centers the line when the water is exactly at eye height and the camera looks level", () => {
    const result = projectFloodLine(pose(), 10, 1.5);
    expect(result.screenY).toBeCloseTo(0, 6);
    expect(result.offScreen).toBe(false);
  });

  it("centers a below-eye-height point once the camera tilts down to meet it", () => {
    const heightM = 1.5;
    const depthM = 0.5;
    const distanceM = 10;
    const pitchDeg = (Math.atan2(heightM - depthM, distanceM) * 180) / Math.PI;

    const result = projectFloodLine(pose({ heightM, pitchDeg }), distanceM, depthM);

    expect(result.screenY).toBeCloseTo(0, 6);
    expect(result.offScreen).toBe(false);
  });

  it("moves the line toward the top of frame as the camera tilts down further", () => {
    const level = projectFloodLine(pose({ pitchDeg: 0 }), 10, 1.5);
    const tilted = projectFloodLine(pose({ pitchDeg: 10 }), 10, 1.5);

    expect(level.screenY).toBe(0);
    expect(tilted.screenY).toBeLessThan(level.screenY as number);
  });

  it("moves the line higher in frame (toward center) as rising water gets closer to eye height", () => {
    const shallow = projectFloodLine(pose(), 10, 0.5);
    const deeper = projectFloodLine(pose(), 10, 1.0);

    // Both are below eye height (1.5m); the deeper one is closer to eye
    // height, so its line sits higher (closer to center) than the shallower one.
    expect(deeper.screenY as number).toBeLessThan(shallow.screenY as number);
  });

  it("reports off-screen instead of clamping when the line falls outside the field of view", () => {
    const result = projectFloodLine(pose({ verticalFovDeg: 10 }), 5, 50);
    expect(result.offScreen).toBe(true);
    expect(result.screenY).toBeNull();
  });

  it("rejects a non-positive distance", () => {
    expect(() => projectFloodLine(pose(), 0, 1.0)).toThrow(/distance/);
    expect(() => projectFloodLine(pose(), -5, 1.0)).toThrow(/distance/);
  });
});
