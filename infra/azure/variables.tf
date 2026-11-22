variable "prefix" {
  description = "Short name used in every resource name."
  type        = string
  default     = "bankrag"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,11}$", var.prefix))
    error_message = "prefix must be 3-12 lowercase letters or digits, starting with a letter."
  }
}

variable "environment" {
  description = "dev, staging or prod. Part of every resource name and tag."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "location" {
  description = "Azure region. Set it to the region your data-residency policy requires, and check Container Apps is offered there."
  type        = string
  default     = "southeastasia"
}

variable "image_tag" {
  description = "Image tag in the registry, e.g. the git SHA CI built and gated. Mutable tags such as 'latest' are refused, so every revision maps to one tested build."
  type        = string

  validation {
    condition     = length(var.image_tag) > 0 && !contains(["latest", "main", "stable"], lower(var.image_tag))
    error_message = "image_tag must be an immutable tag such as a git SHA, not 'latest', 'main' or 'stable'."
  }
}

variable "ingress_external" {
  description = "false: reachable only inside the Container Apps environment (put an API gateway in front). true: public HTTPS endpoint, limited by allowed_ip_ranges."
  type        = bool
  default     = false
}

variable "allowed_ip_ranges" {
  description = "CIDR ranges allowed to call the API. Empty means no IP restriction."
  type        = list(string)
  default     = []
}

variable "infrastructure_subnet_id" {
  description = "Optional subnet (/23 or larger, delegated to Microsoft.App/environments) to run the environment inside an existing VNet."
  type        = string
  default     = null
}

variable "log_retention_days" {
  description = "How long container logs (JSON, with request ids) are kept in Log Analytics."
  type        = number
  default     = 90
}

variable "anchor_retention_days" {
  description = "WORM retention for exported audit anchors. Set it to your record-retention policy."
  type        = number
  default     = 2555
}

variable "tags" {
  description = "Extra tags for every resource."
  type        = map(string)
  default     = {}
}
