export interface SellerImageConfig {
  id: string
  seller_id: string
  raw_base_url: string
  created_at: string
  updated_at: string
}

export interface SellerImageConfigUpsert {
  raw_base_url: string
}
