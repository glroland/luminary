{{- define "luminary.appName" -}}
{{- .Values.global.nameOverride | default "luminary" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "luminary.labels" -}}
app.kubernetes.io/name: {{ include "luminary.appName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/*
Same formula the api/web subcharts' own "api.pullSecretName"/
"web.pullSecretName" helpers compute independently from the same global
naming values -- all three must stay in lock-step so every chart agrees on
this one shared Secret's name without referencing each other directly.
*/}}
{{- define "luminary.pullSecretName" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 52 | trimSuffix "-" -}}-pull-secret
{{- end -}}
