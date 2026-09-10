"use client"

import { Badge } from "@/components/ui/badge"
import type { ListingStatus } from "@/types/listing"
import { STATUS_LABELS } from "@/types/listing"

const STATUS_VARIANTS: Record<
  ListingStatus,
  "default" | "secondary" | "destructive" | "outline"
> = {
  draft: "outline",
  generating_title: "secondary",
  pending_title_approval: "default",
  predicting_category: "secondary",
  pending_seller_attributes: "default",
  pending_description: "secondary",
  generating_images: "secondary",
  pending_raw_photos: "default",
  pending_image_approval: "default",
  generating_description: "secondary",
  ready_to_publish: "default",
  publishing: "secondary",
  published: "default",
  published_paused: "outline",
  failed: "destructive",
}

const C = {
  green:  "bg-green-100  text-green-800  border-green-200  dark:bg-green-900/30  dark:text-green-400  dark:border-green-800",
  blue:   "bg-blue-100   text-blue-800   border-blue-200   dark:bg-blue-900/30   dark:text-blue-400   dark:border-blue-800",
  yellow: "bg-yellow-100 text-yellow-800 border-yellow-200 dark:bg-yellow-900/30 dark:text-yellow-400 dark:border-yellow-800",
  purple: "bg-purple-100 text-purple-800 border-purple-200 dark:bg-purple-900/30 dark:text-purple-400 dark:border-purple-800",
}

const STATUS_COLORS: Partial<Record<ListingStatus, string>> = {
  published:                C.green,
  published_paused:         C.yellow,
  ready_to_publish:         C.blue,
  pending_title_approval:   C.yellow,
  pending_seller_attributes: C.yellow,
  pending_image_approval:   C.yellow,
  pending_raw_photos:       C.yellow,
  generating_title:         C.purple,
  generating_images:        C.purple,
  generating_description:   C.purple,
  predicting_category:      C.purple,
  publishing:               C.purple,
}

interface Props {
  status: ListingStatus
  className?: string
}

export function ListingStatusBadge({ status, className }: Props) {
  const label = STATUS_LABELS[status] || status
  const colorClass = STATUS_COLORS[status]

  if (colorClass) {
    return (
      <span
        className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold ${colorClass} ${className || ""}`}
      >
        {label}
      </span>
    )
  }

  return (
    <Badge variant={STATUS_VARIANTS[status] || "outline"} className={className}>
      {label}
    </Badge>
  )
}
