#!/bin/sh
# Renders the static S3 credential file from the environment, then starts the all-in-one server (master+volume+filer+S3).
# M9: when LANGFUSE_S3_ACCESS_KEY/SECRET are set (make env), a second identity limited to the bucket `langfuse` is added
# for the observability profile; without them the file is exactly the M0 one.
set -eu
mkdir -p /etc/seaweedfs
LANGFUSE_IDENTITY=""
if [ -n "${LANGFUSE_S3_ACCESS_KEY:-}" ] && [ -n "${LANGFUSE_S3_SECRET_KEY:-}" ]; then
  LANGFUSE_IDENTITY=$(cat <<EOF
,
    {
      "name": "langfuse",
      "credentials": [{ "accessKey": "${LANGFUSE_S3_ACCESS_KEY}", "secretKey": "${LANGFUSE_S3_SECRET_KEY}" }],
      "actions": ["Admin:langfuse", "Read:langfuse", "Write:langfuse", "List:langfuse", "Tagging:langfuse"]
    }
EOF
)
fi
cat > /etc/seaweedfs/s3.json <<EOF
{
  "identities": [
    {
      "name": "leave-assistant",
      "credentials": [{ "accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}" }],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }${LANGFUSE_IDENTITY}
  ]
}
EOF
exec /usr/bin/weed server \
  -dir=/data -ip=seaweedfs -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=256 -volume.max=20 \
  -filer=true -s3=true -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
