# Factura Electrónica Panamá — Factura Fácil para Odoo

Módulo de Odoo que emite documentos fiscales electrónicos ante la **DGI de Panamá**
a través del PAC **Factura Fácil**, desde el **Punto de Venta** y desde las
**facturas de Contabilidad**.

## Ramas

Una rama por versión de Odoo, como en el propio Odoo y en la OCA:

| Rama | Odoo | Estado |
|---|---|---|
| [`19.0`](../../tree/19.0) | 19.0 | Estable — en producción |
| [`18.0`](../../tree/18.0) | 18.0 | Portada desde 19.0, **sin probar** |

Usa siempre la rama que corresponde a tu versión de Odoo. El módulo **no** es
compatible entre versiones: la API del POS y el cálculo de las devoluciones
cambian de una a otra.

## Instalación

El módulo es `ff_pos_agent`. Clona la rama de tu versión y añade el directorio al
`addons_path`:

```bash
git clone -b 19.0 https://github.com/Factura-Facil/plugin-odoo19.git
```

```
addons_path = ...,/ruta/a/plugin-odoo19
```

Después, en Odoo: **Aplicaciones → Actualizar lista de aplicaciones** e instalar
*Factura Electrónica Panamá (Factura Fácil) - POS*.

## Modos de emisión

| Modo | Quién llama al agente | Para |
|---|---|---|
| Agente offline | El servidor de Odoo | Odoo en la misma red que el agente |
| Agente en la caja | El navegador del POS | Odoo en la nube, agente en cada caja |
| API en línea | El servidor de Odoo | Sin agente local |

El Punto de Venta y las facturas de Contabilidad se configuran por separado y
pueden usar modos distintos a la vez.

## Documentación

- `ff_pos_agent/CHANGELOG.md` — cambios por versión
- `ff_pos_agent/README.md` — detalle funcional y técnico

## Licencia

OPL-1 (Odoo Proprietary License v1.0). Ver `ff_pos_agent/LICENSE`.
