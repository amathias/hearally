variable "name" {
  description = "Resource name prefix, hearally-prod."
  type        = string
}

variable "domain_name" {
  description = "Apex domain that sends production email."
  type        = string
}

variable "zone_id" {
  description = "Route 53 hosted zone hosting the verification records."
  type        = string
}
