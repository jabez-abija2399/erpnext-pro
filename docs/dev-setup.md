# Dev setup (Claude Code on the web)

How the development bench was built in a cloud session. Paths are the ones used there.

| Piece | Version / location |
|---|---|
| Linux user | `frappe` (bench refuses to run as root) |
| Python | 3.14 at `/home/frappe/py/py314` (copied from `uv python install 3.14`) |
| bench CLI | venv at `/home/frappe/benchenv` |
| Node | 24 at `/opt/node24` (official nodejs.org tarball, checksum verified) |
| MariaDB | 10.11 from apt, utf8mb4 config in `/etc/mysql/mariadb.conf.d/99-frappe.cnf`, local root password `devroot` |
| Redis | `redis-server config/redis_cache.conf` and `config/redis_queue.conf` from the bench |
| Bench | `/home/frappe/frappe-bench`, Frappe and ERPNext `version-16` |
| Site | `erp.localhost`, Administrator password `admin` (local test site only) |

The passwords above exist only on throwaway cloud machines. Never reuse them anywhere real.

## Steps
1. `apt-get install -y mariadb-server mariadb-client libmariadb-dev pkg-config`
2. Write the utf8mb4 config, start MariaDB with `mysqld_safe`, set the root password.
3. Create user `frappe`; give it Python 3.14 and a venv with `pip install frappe-bench`.
4. Install Node 24 and put it first on `PATH`.
5. As `frappe`: `bench init frappe-bench --frappe-branch version-16 --python <py3.14>`
6. `bench get-app erpnext https://github.com/frappe/erpnext --branch version-16`
7. Start both Redis servers, then
   `bench new-site erp.localhost --db-root-username root --db-root-password devroot --admin-password admin --install-app erpnext`
8. `bench get-app https://github.com/jabez-abija2399/erpnext-pro` and `bench --site erp.localhost install-app erpnext_pro`

## Network
`bench build` downloads `frappe-ui` from `codeload.github.com`, so the cloud environment must allow that host.
The environment "ERPNext Pro" allows it. Without it, use `--skip-assets` and build later.
