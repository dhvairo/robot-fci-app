# Robot FCI - pantallas de consulta

Código de las pantallas (Streamlit) que consultan la base de datos de fondos comunes de inversión.
Este repositorio **solo contiene código**: los datos están en una base privada, a la que la app se
conecta con un secreto (`APP_DB_URL`, de solo lectura) y pide una clave de acceso (`APP_PASSWORD`). Un segundo
secreto (`PEDIDOS_DB_URL`) tiene el permiso mínimo para anotar pedidos de seguir o dejar de seguir fondos.

Se genera automáticamente desde el proyecto principal: no editar acá.

Pantallas: Inicio, Ficha por fondo, Comparador, Buscador de instrumentos, Filas a revisar, Resumen masivo de fondos,
Gestión y eficiencia, Sumar o quitar fondos.
Archivo principal: `Inicio.py`.
