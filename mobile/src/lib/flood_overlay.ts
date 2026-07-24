/**
 * Flood-line overlay projection — the geometry half of AR mode (phase 4,
 * milestone 6, docs/plans/phase-4-scale.md). Given a predicted water depth
 * at the point the camera is looking toward (from GET /map/inundation/point
 * — see backend/app/modules/inundation/service.py::predicted_depth_at_point)
 * and the device's own pose, this computes where on screen a horizontal
 * flood line should render.
 *
 * This is pure trigonometry, not real AR: it assumes a flat ground plane
 * between the device and the target (the same simplification the bathtub
 * model itself already documents — inundation/engine.py's own docstring:
 * "no flow routing... a low-lying cell cut off from the sea by a ridge
 * still floods here, same as an actual bathtub"), a known horizontal
 * distance to the target rather than a depth-sensed one, and no lens
 * distortion correction.
 *
 * It answers "where would the line go" given those inputs; it does not
 * answer "how do I get those inputs from a real camera." Device pose
 * (pitch, distance-to-target) and the camera preview itself are a native
 * rendering problem this environment has no physical device or emulator to
 * build against or verify — the same limitation mesh.ts already carries for
 * BLE/Wi-Fi Direct (see that file's own docstring) and mobile/README.md's
 * "Not built" list. The seam here is real and tested; the camera screen
 * that would call it is not built in this environment.
 */

export interface DevicePose {
  /** Height of the camera above the ground it's standing on, in meters — a
   *  fixed assumed eye/hand height for a spike. A real build would let the
   *  user calibrate this or read it off AR plane detection. */
  heightM: number;
  /** Downward tilt of the camera's optical axis from horizontal, in
   *  degrees. 0 = looking at the horizon, positive = tilted down. */
  pitchDeg: number;
  /** Vertical field of view of the camera, in degrees. */
  verticalFovDeg: number;
}

export interface OverlayResult {
  /** Vertical screen position of the flood line: -1 (top edge) to +1
   *  (bottom edge), 0 = dead center. Null when the line falls outside the
   *  current field of view. */
  screenY: number | null;
  /** True if the computed line falls outside the frame — the caller should
   *  render nothing rather than clamp to an edge, since clamping would draw
   *  a false line that isn't where the water actually is. */
  offScreen: boolean;
}

/**
 * distanceM: horizontal ground distance from the device to the point the
 * depth reading applies to. The caller supplies this (e.g. a fixed demo
 * distance, or a future depth-sensing API) — measuring it isn't solved here.
 *
 * depthM: predicted_depth_at_point()'s depth_m. Callers should not call this
 * function at all when depth_m is null (that point isn't predicted to
 * flood) — there's no line to draw, and that check belongs to the caller.
 */
export function projectFloodLine(pose: DevicePose, distanceM: number, depthM: number): OverlayResult {
  if (distanceM <= 0) {
    throw new Error("distanceM must be positive — a device can't measure distance to itself");
  }
  const angleBelowHorizontalDeg = radToDeg(Math.atan2(pose.heightM - depthM, distanceM));
  const angleFromOpticalAxisDeg = angleBelowHorizontalDeg - pose.pitchDeg;
  const screenY = angleFromOpticalAxisDeg / (pose.verticalFovDeg / 2);
  if (screenY < -1 || screenY > 1) {
    return { screenY: null, offScreen: true };
  }
  return { screenY, offScreen: false };
}

function radToDeg(rad: number): number {
  return (rad * 180) / Math.PI;
}
