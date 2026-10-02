variable "domain_name" {
  description = "Production apex domain delegated from the registrar."
  type        = string
}

variable "subdomains" {
  description = "Subdomains covered by the production certificate, per design document section 10."
  type        = list(string)
  default     = ["www", "portal", "api", "auth"]
}
