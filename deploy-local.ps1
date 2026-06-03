param(
  [string]$AppVersion = "dev",
  [string]$DockerRegistry = "local",
  [switch]$Build,
  [switch]$NoCache,
  [switch]$SkipTranslationPreload
)

$ErrorActionPreference = "Stop"

$env:APP_VERSION = $AppVersion
$env:DOCKER_REGISTRY = $DockerRegistry

if (-not $env:NACOS_USERNAME) {
  $env:NACOS_USERNAME = "nacos"
}
if (-not $env:NACOS_PASSWORD) {
  $env:NACOS_PASSWORD = "oriole003"
}
if ($SkipTranslationPreload) {
  $env:PRELOAD_TRANSLATION_MODELS = "false"
} elseif (-not $env:PRELOAD_TRANSLATION_MODELS) {
  $env:PRELOAD_TRANSLATION_MODELS = "true"
}

if (-not (docker network ls --filter name=^wisepen-net$ -q)) {
    docker network create wisepen-net | Out-Null
}

$composeFiles = @(
  "-f", "docker-compose-app.yml",
  "-f", "docker-compose-build.yml",
  "-f", "docker-compose-dev.yml"
)

if (docker network inspect cloud-infra_cloud-net 2>$null) {
  $composeFiles += @("-f", "docker-compose-app.legacy-net.yml")
}

if ($Build) {
  $buildArgs = @("compose", "--progress=plain") + $composeFiles + @("build", "chat-service")
  if ($NoCache) {
    $buildArgs += "--no-cache"
  }

  Write-Host "Building chat-service image from current source..."
  docker @buildArgs
}

Write-Host "Starting chat-service in dev bind-mount mode..."
$upArgs = @("compose") + $composeFiles + @("up", "-d", "--force-recreate", "--remove-orphans", "chat-service")
docker @upArgs

Write-Host "chat-service image:"
docker images "$DockerRegistry/wisepencloud-chat" --format "  {{.Repository}}:{{.Tag}} {{.ID}} {{.CreatedSince}}"

Write-Host "chat-service container:"
docker ps --filter "name=wisepen-chat-service" --format "  {{.Names}} {{.Image}} {{.Status}}"
