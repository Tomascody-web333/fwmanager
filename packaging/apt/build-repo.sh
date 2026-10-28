#!/bin/sh
# Builds a signed APT repository from a folder of fwmanager .deb files.
#
#   packaging/apt/build-repo.sh DEB_DIR OUT_DIR KEY
#
# KEY is the GPG key (fingerprint, id or e-mail) in the current keyring that signs the
# repository; a passphrase, if the key has one, is read from APT_GPG_PASSPHRASE. OUT_DIR gets:
#   pool/main/*.deb               the packages
#   dists/stable/...              the indexes, signed (InRelease and Release.gpg)
#   fwmanager.asc                 the public key people add once
#   index.html                    how to add the repository
# Needs apt-ftparchive (apt-utils) and gpg.
set -eu

die() { echo "build-repo: $1" >&2; exit 1; }
[ $# -eq 3 ] || die "usage: build-repo.sh DEB_DIR OUT_DIR KEY"
DEBS=$1
OUT=$2
KEY=$3
URL=${APT_REPO_URL:-https://tomascody-web333.github.io/fwmanager}
ARCHES="all amd64 arm64 armhf i386"  # Architecture: all, listed for every machine apt asks for

command -v apt-ftparchive >/dev/null 2>&1 || die "apt-ftparchive not found (apt install apt-utils)"
command -v gpg >/dev/null 2>&1 || die "gpg not found"
ls "$DEBS"/*.deb >/dev/null 2>&1 || die "no .deb files in $DEBS"
gpg --batch --list-secret-keys "$KEY" >/dev/null 2>&1 || die "no secret key $KEY in the keyring"

sign() {  # gpg options...
    if [ -n "${APT_GPG_PASSPHRASE:-}" ]; then
        printf '%s' "$APT_GPG_PASSPHRASE" | gpg --batch --yes --pinentry-mode loopback \
            --passphrase-fd 0 --local-user "$KEY" --digest-algo SHA512 "$@"
    else
        gpg --batch --yes --local-user "$KEY" --digest-algo SHA512 "$@"
    fi
}

rm -rf "$OUT"
install -d "$OUT/pool/main"
cp "$DEBS"/*.deb "$OUT/pool/main/"
cd "$OUT"

for arch in $ARCHES; do
    dir=dists/stable/main/binary-$arch
    install -d "$dir"
    apt-ftparchive packages pool > "$dir/Packages" 2> ftparchive.err
    # apt-ftparchive reports a broken package but still exits 0.
    if grep -q '^E:' ftparchive.err; then
        cat ftparchive.err >&2
        die "a package could not be read"
    fi
    rm -f ftparchive.err
    gzip -9 -n -k "$dir/Packages"
done
apt-ftparchive \
    -o APT::FTPArchive::Release::Origin=fwmanager \
    -o APT::FTPArchive::Release::Label=fwmanager \
    -o APT::FTPArchive::Release::Suite=stable \
    -o APT::FTPArchive::Release::Codename=stable \
    -o "APT::FTPArchive::Release::Architectures=$ARCHES" \
    -o APT::FTPArchive::Release::Components=main \
    -o "APT::FTPArchive::Release::Description=fwmanager, a friendly firewall for ufw" \
    release dists/stable > dists/stable/Release.tmp
mv dists/stable/Release.tmp dists/stable/Release
sign --clearsign -o dists/stable/InRelease dists/stable/Release
sign --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
gpg --batch --armor --export "$KEY" > fwmanager.asc
[ -s fwmanager.asc ] || die "could not export the public key"
FPR=$(gpg --batch --with-colons --fingerprint "$KEY" | awk -F: '/^fpr:/ {print $10; exit}')

cat > index.html <<HTML
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>fwmanager APT repository</title>
<style>body{font-family:system-ui,sans-serif;max-width:46rem;margin:2rem auto;padding:0 1rem;line-height:1.5}
pre{background:#f3f3f3;padding:1rem;overflow-x:auto}</style></head>
<body>
<h1>fwmanager</h1>
<p>A friendly firewall for ufw, with a profile per network. / Uma firewall simples para o ufw, com um perfil por rede.</p>
<h2>Install / Instalar (Ubuntu 24.04+)</h2>
<pre>sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL $URL/fwmanager.asc | sudo tee /etc/apt/keyrings/fwmanager.asc &gt;/dev/null
echo "deb [signed-by=/etc/apt/keyrings/fwmanager.asc] $URL stable main" \\
  | sudo tee /etc/apt/sources.list.d/fwmanager.list
sudo apt update
sudo apt install fwmanager</pre>
<p>Updates arrive with <code>sudo apt upgrade</code>. / As atualizações chegam com <code>sudo apt upgrade</code>.</p>
<p>Signing key / Chave de assinatura: <code>$FPR</code></p>
<p><a href="https://github.com/Tomascody-web333/fwmanager">Source code / Código</a></p>
</body></html>
HTML
echo "$OUT"
