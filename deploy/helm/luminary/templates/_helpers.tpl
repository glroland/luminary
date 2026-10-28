{{- define "luminary.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "luminary.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- include "luminary.name" . | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "luminary.labels" -}}
app.kubernetes.io/name: {{ include "luminary.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "luminary.apiFullname" -}}
{{ include "luminary.fullname" . }}-api
{{- end -}}

{{- define "luminary.webFullname" -}}
{{ include "luminary.fullname" . }}-web
{{- end -}}
