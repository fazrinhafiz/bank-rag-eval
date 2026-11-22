output "api_fqdn" {
  description = "Host name of the API (internal to the environment unless ingress_external = true)."
  value       = azurerm_container_app.api.ingress[0].fqdn
}

output "registry_login_server" {
  description = "Push the gated image here: <server>/bankrag:<git sha>."
  value       = azurerm_container_registry.main.login_server
}

output "key_vault_name" {
  description = "Set bankrag-audit-key and bankrag-trace-salt here before the first revision starts."
  value       = azurerm_key_vault.main.name
}

output "anchor_container" {
  description = "Write-once blob container for exported audit anchors (bankrag audit head)."
  value       = "${azurerm_storage_account.main.name}/${azurerm_storage_container.anchors.name}"
}

output "app_identity_client_id" {
  description = "Client id of the app's managed identity."
  value       = azurerm_user_assigned_identity.app.client_id
}
