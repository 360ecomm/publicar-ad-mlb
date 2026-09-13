import { apiFetch } from "./client"

export interface SellerOut {
  id: string
  ml_user_id: number
  ml_nickname: string
  ml_site_id: string
  token_expires_at: string
  is_active: boolean
  granted_at: string
}

export interface ListingStatusCount {
  status: string
  count: number
}

export interface SellerDashboardEntry {
  seller_id: string
  ml_nickname: string
  is_active: boolean
  listings_by_status: Record<string, number>
  total_listings: number
  last_activity_at: string | null
}

export interface DashboardResponse {
  sellers: SellerDashboardEntry[]
}

export async function listSellers(): Promise<SellerOut[]> {
  return apiFetch<SellerOut[]>("/api/v1/sellers")
}

export async function getDashboard(): Promise<DashboardResponse> {
  return apiFetch<DashboardResponse>("/api/v1/dashboard")
}

export async function disconnectSeller(id: string): Promise<SellerOut> {
  return apiFetch<SellerOut>(`/api/v1/sellers/${id}/disconnect`, { method: "POST" })
}
