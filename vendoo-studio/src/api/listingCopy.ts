export interface CopyPreview {
  source_id: string;
  source_title: string;
  expected_basis: string;
  photo_count: number;
  has_listing: boolean;
  title: string;
}
export interface CopyListingRequest {
  expected_basis: string;
  title: string;
  sku: string;
  quantity: number;
  include_photos: boolean;
  include_cost: boolean;
}
