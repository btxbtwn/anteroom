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

export interface ListingVideoLibrary {
  videos: ListingVideo[];
  max_bytes: number;
}
