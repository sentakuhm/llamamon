# Arch Linux package for llamamon.
#
# Build with:  makepkg -f
# (run this inside a checkout of the llamamon source tree; the source tarball
# is the tagged release from GitHub)
pkgname=llamamon
pkgver=1.1.1
pkgrel=1
pkgdesc="Terminal monitor for a local llama.cpp inference server"
arch=(any)
url="https://github.com/sentakuhm/llamamon"
license=(MIT)
depends=(python python-rich python-textual python-psutil)
source=("$url/archive/refs/tags/v${pkgver}.tar.gz")
sha256sums=("7b02354a1f7d7cb5c413a86e7791d2d16a1a43c22cc4c06ec8e0bc19d3020d5f")

package() {
  install -d "$pkgdir/usr/bin"
  install -d "$pkgdir/usr/lib/llamamon/llamamon"
  install -m644 "$srcdir/$pkgname-$pkgver/"*.py "$pkgdir/usr/lib/llamamon/llamamon/"
  install -Dm644 "$srcdir/$pkgname-$pkgver/LICENSE" \
    "$pkgdir/usr/share/licenses/llamamon/LICENSE"
  cat > "$pkgdir/usr/bin/llamamon" <<'EOF'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/llamamon")
from llamamon.cli import main
raise SystemExit(main())
EOF
  chmod 755 "$pkgdir/usr/bin/llamamon"
}
