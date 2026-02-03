# Factura Facil Panama - Conector para Odoo 19

Modulo de Odoo 19 para integrar la facturacion electronica de Panama con el API de [Factura Facil](https://facturafacil.com.pa).

Permite enviar facturas de cliente y notas de credito directamente desde Odoo al sistema de facturacion electronica de la DGI de Panama.

---

## Caracteristicas

- Envio manual o automatico de facturas electronicas al confirmar
- Soporte para **facturas de cliente** (`out_invoice`) y **notas de credito** (`out_refund`)
- Mapeo de impuestos ITBMS (codigos 00, 01, 02, 03)
- Recepcion y almacenamiento del **CUFE** (Codigo Unico de Factura Electronica)
- Historial completo de envios con request/response JSON
- Reintentos automaticos via cron con exponential backoff
- Ambientes QA y Produccion configurables
- Boton de prueba de conexion

---

## Requisitos

- **Odoo 19** (Community o Enterprise)
- Modulo `account` (Contabilidad) instalado
- Cuenta activa en [Factura Facil Panama](https://facturafacil.com.pa) con credenciales de API
- Python: libreria `requests` (incluida en Odoo)

---

## Instalacion

### 1. Descargar el modulo

Clonar este repositorio en el directorio de addons personalizados de Odoo:

```bash
cd /opt/odoo19/custom-addons
git clone https://gitlab.grupo-ari.net/factura-facil/odoo-plugin.git facturafacil_connector
```

> Si no tienes un directorio `custom-addons`, crealo y agreguelo a la configuracion de Odoo.

### 2. Configurar addons_path

Asegurese de que el archivo de configuracion de Odoo (`/etc/odoo19.conf` o similar) incluya la ruta a `custom-addons`:

```ini
[options]
addons_path = /opt/odoo19/addons,/opt/odoo19/custom-addons
```

### 3. Reiniciar Odoo

```bash
sudo systemctl restart odoo19
```

### 4. Instalar el modulo

1. Acceder a Odoo como administrador
2. Activar el **modo desarrollador**: Ajustes > General > Herramientas de Desarrollador > Activar modo desarrollador
3. Ir a **Aplicaciones** > **Actualizar lista de Aplicaciones**
4. Buscar **"Factura Facil"**
5. Hacer clic en **Instalar**

---

## Configuracion

### Credenciales del API

1. Ir a **Contabilidad > Configuracion > Ajustes**
2. Buscar la seccion **Factura Facil Panama**
3. Completar los siguientes campos:

| Campo | Descripcion |
|-------|-------------|
| **Ambiente** | `QA (Pruebas)` o `Produccion` - la URL del API se ajusta automaticamente |
| **X-FF-Company** | UUID de la empresa proporcionado por Factura Facil |
| **X-FF-Branch** | UUID de la sucursal proporcionado por Factura Facil |
| **X-FF-API-Key** | Llave de API proporcionada por Factura Facil |
| **Codigo de Sucursal** | Codigo de 4 caracteres (default: `0000`) |
| **Numero de Caja** | Numero de punto de venta de 3 digitos (default: `001`) |
| **Maximo de Reintentos** | Intentos automaticos para envios fallidos (default: `3`) |
| **Envio Automatico** | Si se activa, la factura se envia automaticamente al confirmarla |

4. Hacer clic en **Probar Conexion** para verificar que las credenciales son correctas
5. **Guardar** los ajustes

### Impuestos ITBMS

El modulo agrega un campo **Codigo ITBMS** a los impuestos de Odoo. Para configurarlo:

1. Ir a **Contabilidad > Configuracion > Impuestos**
2. Abrir cada impuesto ITBMS
3. En la pestana **Factura Facil Panama**, asignar el codigo correspondiente:

| Codigo | Descripcion | Tasa |
|--------|-------------|------|
| `00` | ITBMS Exento | 0% |
| `01` | ITBMS 7% | 7% |
| `02` | ITBMS 10% | 10% |
| `03` | ITBMS 15% | 15% |

### Datos del Receptor (Contactos)

El modulo agrega campos de facturacion electronica a los contactos. Para configurarlos:

1. Ir a **Contactos**
2. Abrir el contacto
3. En la pestana **Facturacion Electronica (PA)**, completar:

| Campo | Descripcion |
|-------|-------------|
| **Tipo de Receptor** | `01` Contribuyente, `02` Consumidor Final, `03` Gobierno, `04` Extranjero |
| **Tipo de RUC** | `1` Natural, `2` Juridico |
| **Digito Verificador** | DV del RUC del receptor |

> El **RUC** se toma del campo estandar **NIF** (Tax ID / `vat`) del contacto.

---

## Uso

### Envio manual

1. Crear y confirmar una factura de cliente o nota de credito
2. En la factura confirmada, hacer clic en el boton **"Enviar a Factura Facil"**
3. El estado FE cambiara a:
   - **Validado (CUFE)**: envio exitoso, el CUFE se muestra en la factura
   - **Rechazado**: el API rechazo la factura (ver detalle del error)
   - **Error**: error de conexion o timeout

### Envio automatico

Si se activa la opcion **"Envio Automatico"** en la configuracion, cada factura de cliente y nota de credito se enviara automaticamente al momento de confirmarla.

### Reintentar envio

- En la factura, hacer clic en **"Reintentar Envio"** para intentar nuevamente
- El cron de reintentos automaticos se ejecuta cada **30 minutos** y reintenta los envios fallidos con backoff exponencial (1, 2, 4, 8 minutos entre intentos)

### Ver logs de envio

- **En la factura**: pestana **"Factura Electronica"** > seccion "Historial de Envios"
- **Menu global**: **Contabilidad > Factura Electronica > Logs de Envios**

Cada log incluye:
- Estado del envio (exitoso, error, timeout)
- Codigo HTTP de respuesta
- CUFE recibido (si exitoso)
- Tiempo de respuesta en milisegundos
- JSON completo del request y response

---

## Estructura del modulo

```
facturafacil_connector/
├── __init__.py
├── __manifest__.py
├── models/
│   ├── __init__.py
│   ├── account_move.py          # Logica de envio al API
│   ├── account_tax.py           # Campo ITBMS en impuestos
│   ├── ff_log.py                # Modelo de logs
│   ├── res_config_settings.py   # Configuracion de credenciales
│   └── res_partner.py           # Campos FE en contactos
├── views/
│   ├── account_move_views.xml   # Botones y pestana FE en facturas
│   ├── account_tax_views.xml    # Pestana ITBMS en impuestos
│   ├── ff_log_views.xml         # Vistas de logs + menu
│   ├── res_config_settings_views.xml  # Seccion en Ajustes
│   └── res_partner_views.xml    # Campos FE en contactos
├── security/
│   ├── ir.model.access.csv      # Permisos de acceso
│   └── security.xml             # Grupos de seguridad
└── data/
    └── cron.xml                 # Cron de reintentos automaticos
```

---

## Grupos de seguridad

| Grupo | Permisos |
|-------|----------|
| **Usuario FE** | Puede enviar facturas y ver estado FE |
| **Administrador FE** | Configurar credenciales, ver logs completos |

Asigne estos grupos a los usuarios desde **Ajustes > Usuarios > [Usuario] > Pestana Accesos**.

---

## Endpoints del API

| Ambiente | URL |
|----------|-----|
| QA (Pruebas) | `https://backend-qa-api.facturafacil.com.pa/api/pac/reception_fe/detailed/` |
| Produccion | `https://api.facturafacil.com.pa/api/pac/reception_fe/detailed/` |

---

## Soporte

- **Factura Facil Panama**: https://facturafacil.com.pa
- **Grupo ARI**: https://grupo-ari.net
- **Reportar problemas**: Crear un issue en este repositorio

---

## Licencia

LGPL-3 - Ver archivo [LICENSE](LICENSE) para mas detalles.
