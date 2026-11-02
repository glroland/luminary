{{- define "web.appName" -}}
{{- .Values.global.nameOverride | default "luminary" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "web.fullname" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 59 | trimSuffix "-" -}}-web
{{- end -}}

{{- define "web.pullSecretName" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 52 | trimSuffix "-" -}}-pull-secret
{{- end -}}

{{/*
The api Service's name -- the api subchart's own "api.fullname" helper
computes this identically from the same global naming values, so the two
charts agree on it without needing a direct reference between them.
*/}}
{{- define "web.apiFullname" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 59 | trimSuffix "-" -}}-api
{{- end -}}

{{- define "web.labels" -}}
app.kubernetes.io/name: {{ include "web.appName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
