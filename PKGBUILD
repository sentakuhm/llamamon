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
sha256sums=("7500afeec056d33b4a597a11c0f009558b2752e8d01e7f13e1630dc594836dab")

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
