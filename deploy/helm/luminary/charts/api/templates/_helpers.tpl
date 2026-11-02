{{- define "api.appName" -}}
{{- .Values.global.nameOverride | default "luminary" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "api.fullname" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 59 | trimSuffix "-" -}}-api
{{- end -}}

{{- define "api.pullSecretName" -}}
{{- $base := .Values.global.fullnameOverride | default (.Values.global.nameOverride | default "luminary") -}}
{{- $base | trunc 52 | trimSuffix "-" -}}-pull-secret
{{- end -}}

{{- define "api.labels" -}}
app.kubernetes.io/name: {{ include "api.appName" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
