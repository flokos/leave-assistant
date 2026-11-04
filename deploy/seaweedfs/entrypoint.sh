#!/bin/sh
# Renders the static S3 credential file from the environment, then starts the all-in-one server (master+volume+filer+S3).
set -eu
mkdir -p /etc/seaweedfs
cat > /etc/seaweedfs/s3.json <<EOF
{
  "identities": [
    {
      "name": "leave-assistant",
      "credentials": [{ "accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}" }],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
EOF
exec /usr/bin/weed server \
  -dir=/data -ip=seaweedfs -ip.bind=0.0.0.0 \
  -master.volumeSizeLimitMB=256 -volume.max=20 \
  -filer=true -s3=true -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json
