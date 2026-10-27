export interface ListingVideo {
  id: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  duration_seconds: number;
  width: number;
  height: number;
  url: string;
}

/** One marketplace's limits, from Anteroom's server. `shorter_than` is exclusive. */
export interface ListingVideoRules {
  name: string;
  shorter_than?: number;
  min_seconds?: number;
  max_seconds?: number;
  max_bytes?: number;
  size_label?: string;
  mime_types?: string[];
}

export interface ListingVideoLibrary {
  videos: ListingVideo[];
  max_bytes: number;
  rules?: Record<string, ListingVideoRules>;
}

/** Why the marketplace would refuse this video, or null when it fits. Mirrors the server's check. */
export function videoProblem(video: ListingVideo, rules: ListingVideoRules | undefined): string | null {
  if (!rules) return null;
  if (rules.shorter_than !== undefined && video.duration_seconds >= rules.shorter_than) {
    return `Choose a video shorter than ${rules.shorter_than} seconds for ${rules.name}.`;
  }
  if (video.duration_seconds < (rules.min_seconds ?? 0) || video.duration_seconds > (rules.max_seconds ?? Infinity)) {
    return `Choose a video from ${rules.min_seconds} to ${rules.max_seconds} seconds long for ${rules.name}.`;
  }
  if (video.size_bytes > (rules.max_bytes ?? Infinity)) return `${rules.name} accepts videos up to ${rules.size_label}.`;
  if (rules.mime_types && !rules.mime_types.includes(video.mime_type)) return `${rules.name} needs an MP4 or MOV video.`;
  return null;
}
