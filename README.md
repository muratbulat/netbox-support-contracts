# NetBox Support Contracts

Support contract, warranty coverage, and hardware end-of-life (EoL) tracking for [NetBox](https://github.com/netbox-community/netbox).

Record vendor support contracts and link them to the devices, virtual machines, and modules already in NetBox. You
never have to create a device just to cover it. Add coverage by picking objects, by filtering on site, device type,
or role, or by pasting thousands of serial numbers straight from a spreadsheet, invoice, or vendor quote.

| | |
|---|---|
| NetBox | 4.7.0 or later (no upper bound declared) |
| Python | 3.12 – 3.14 |
| License | Apache-2.0 |

## Features

- **Support contracts**: manufacturer, contract number (unique per manufacturer, case-insensitive), service level,
  term, reseller, vendor portal link, renewal notes, tenant, and commercial cost.
- **Live state, never stale**: *Active*, *Expiring*, and *Expired* states and the days remaining are computed from the
  end date whenever they're read; nothing is stored. A blank end date means open-ended, and the end date is the last
  covered day.
- **Coverage**: link a contract to existing devices, VMs, or modules. Optionally set an end date for a single item
  (the contract's end date applies otherwise) plus a SKU or license part number. A row with only a SKU records a
  contract-level license that isn't tied to one object.
- **Bulk coverage workflow**: on a contract, choose **Add Devices / Serials** and:
  - **Option A**: pick devices, VMs, or modules, or cover every device matching a site / device type / role filter.
  - **Option B**: paste up to 5,000 serial numbers, separated by newlines, commas, semicolons, or spaces. Matching
    runs in one query and ignores case. Devices already covered are skipped. A serial number shared by more than one
    device is reported as *ambiguous* and never guessed. Any serial numbers that are unmatched or ambiguous are put
    back into the form so you can fix them.
  - **Remove Selected** on the *Covered Devices & Objects* tab removes coverage in bulk.
- **Device and VM panel**: a *Support & Warranty Coverage* card showing the overall coverage badge (Covered,
  Expiring, Expired, or Not covered), the effective end date and days remaining, the covering contracts, and an
  **Assign Contract** button.
- **Hardware lifecycle**: End-of-Sale, End-of-Maintenance, and End-of-Support dates per device type or module type,
  shown on the type's page. Device panels flag conflicts:
  - *End-of-life hardware under active support contract*
  - *Support contract runs past hardware End of Support (EoS)*
- **Device Coverage report** (*Support Contracts → Device Coverage*): every device with its coverage status,
  filterable by status (including *Not covered*), site, role, manufacturer, and device type.
- **Dashboard widget**: *Support Coverage Summary* shows Covered, Expiring, Expired, and Not Covered device counts,
  each linking to the report, plus the soonest-ending contracts.
- **Expiry notifications**: a daily system job raises the events *Support contract expiring* and *Support contract
  expired*. Users subscribed to a contract get a NetBox notification, and Event Rules can trigger webhooks, scripts,
  or notification groups.
- **Built on NetBox**: object permissions, tags, custom fields, journaling, change logging, global search, CSV/YAML
  import and export, bulk edit and delete, REST API, and GraphQL.

## Installation

1. Install the package into NetBox's virtual environment:

   ```shell
   source /opt/netbox/venv/bin/activate
   pip install netbox-support-contracts
   ```

   To install automatically on NetBox upgrades, add `netbox-support-contracts` to `local_requirements.txt`.

2. Enable the plugin in `configuration.py`:

   ```python
   PLUGINS = ["netbox_support_contracts"]
   ```

3. Apply the database migrations and restart NetBox:

   ```shell
   cd /opt/netbox/netbox
   python manage.py migrate
   sudo systemctl restart netbox netbox-rq
   ```

The daily expiry job is scheduled automatically by NetBox's RQ worker (`netbox-rq`).

## Configuration

All settings are optional:

```python
PLUGINS_CONFIG = {
    "netbox_support_contracts": {
        # Contracts and coverage ending within this many days count as "expiring".
        "warning_days": 90,
        # True: a top-level "Support Contracts" menu. False: items under the shared "Plugins" menu.
        "top_level_menu": True,
    },
}
```

## Permissions

The plugin's models use standard NetBox object permissions: *Support Contract*, *Support Coverage*, and *Hardware
Lifecycle (EoL)*.

**Commercial cost** (`cost` and `currency`) is hidden everywhere unless the user is a superuser or holds the custom
action `view_cost` on *Support Contract*. To grant it, create a permission under *Admin → Permissions* with object type
*Support Contract* and tick `view_cost` under *Additional actions*. In code, the permission is named
`netbox_support_contracts.view_cost_supportcontract`.

Without `view_cost`, users don't see the cost card row, the table column (including CSV export), the edit, import, or
bulk-edit fields, the REST API fields, or the GraphQL values (which return `null`). Cost and currency can't be used as
filters. Writing `cost` through the API without the permission is rejected with `400`. Note that NetBox's change log
records every field value; restrict *Core → Object Changes* if you need cost hidden there too.

The bulk coverage actions require `add` (or, for removal, `delete`) on *Support Coverage*, plus `view` on the
contract and on the objects being covered. If any created or removed row violates a permission constraint, the whole
operation is rolled back.

## REST API

Base path: `/api/plugins/support-contracts/`

| Endpoint | Description |
|---|---|
| `contracts/` | Support contracts. Includes computed `state`, `days_remaining`, `device_count`, `vm_count`, `module_count`. Filters include `state`, `expiring_within_days`, `manufacturer_id`, `tenant_id`, `device_id`. |
| `contracts/summary/` | `GET`: dashboard summary of device coverage counts and the soonest-ending contracts. |
| `coverage/` | Coverage rows, with `effective_end_date`, `state`, `days_remaining`. |
| `coverage/bulk-add/` | `POST`: cover objects by ID and/or serial number. |
| `coverage/bulk-remove/` | `POST`: remove coverage by coverage ID or covered-object ID. |
| `lifecycle/` | Hardware lifecycle (EoL) records. |

Bulk add example:

```http
POST /api/plugins/support-contracts/coverage/bulk-add/
{
  "contract_id": 1,
  "device_ids": [10, 11],
  "serials": "SN-10001\nSN-10002, SN-10003",
  "sku": "SUP-8X5-NBD"
}
```

`serials` accepts pasted text or a list of strings. The response lists the coverage rows `created`, the objects that
were `existing` (already covered), the devices `matched` by serial, and the `unmatched` and `ambiguous` serials. The
status is `201` if anything was created, otherwise `200`.

```http
POST /api/plugins/support-contracts/coverage/bulk-remove/
{"contract_id": 1, "device_ids": [10]}
```

## GraphQL

The queries are `support_contract` / `support_contract_list`, `support_coverage` / `support_coverage_list`, and
`hardware_lifecycle_eol` / `hardware_lifecycle_eol_list`. The computed `state`, `days_remaining`, and
`effective_end_date` fields are included.

## Development

Run the test suite inside a NetBox checkout with the plugin installed and enabled. Django creates and destroys a
throwaway test database:

```shell
cd /opt/netbox/netbox
python manage.py test /path/to/netbox-support-contracts/tests
ruff check /path/to/netbox-support-contracts
```
