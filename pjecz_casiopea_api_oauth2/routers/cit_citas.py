"""
Cit Citas, routers
"""

from datetime import datetime, timedelta
from typing import Annotated

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_pagination.ext.sqlalchemy import paginate
from sqlalchemy.exc import MultipleResultsFound, NoResultFound

from ..config.settings import Settings, get_settings
from ..dependencies.authentications import get_current_active_user
from ..dependencies.control_acceso import decodificar_imagen, generar_referencia
from ..dependencies.database import Session, get_db
from ..dependencies.fastapi_pagination_custom_page import CustomPage
from ..dependencies.pwgen import generar_codigo_asistencia
from ..dependencies.safe_string import safe_clave, safe_string, safe_uuid
from ..models.cit_citas import CitCita
from ..models.cit_dias_inhabiles import CitDiaInhabil
from ..models.cit_oficinas_servicios import CitOficinaServicio
from ..models.cit_servicios import CitServicio
from ..models.oficinas import Oficina
from ..models.permisos import Permiso
from ..schemas.cit_citas import CitCitaIn, CitCitaOut, OneCitCitaOut
from ..schemas.cit_clientes import CitClienteInDB
from .cit_dias_disponibles import listar_dias_disponibles
from .cit_horas_disponibles import listar_horas_disponibles

LIMITE_CITAS_PENDIENTES = 3

cit_citas = APIRouter(prefix="/api/v5/cit_citas")


@cit_citas.patch("/cancelar", response_model=OneCitCitaOut)
async def cancelar(
    current_user: Annotated[CitClienteInDB, Depends(get_current_active_user)],
    database: Annotated[Session, Depends(get_db)],
    cit_cita_id: str,
):
    """Cancelar una cita"""
    if current_user.permissions.get("CIT CITAS", 0) < Permiso.CREAR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Consultar, validar que le pertenezca, que no esté eliminada o que no sea PENDIENTE
    try:
        cit_cita_uuid = safe_uuid(cit_cita_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No es válida la UUID")
    cit_cita = database.query(CitCita).get(cit_cita_uuid)
    if not cit_cita:
        return OneCitCitaOut(success=False, message="No existe esa cita")
    if cit_cita.cit_cliente_id != current_user.id:
        return OneCitCitaOut(success=False, message="No le pertenece esa cita")
    if cit_cita.estatus != "A":
        return OneCitCitaOut(success=False, message="No está habilitada esa cita")
    if cit_cita.estado != "PENDIENTE":
        return OneCitCitaOut(success=False, message="No se puede cancelar esta cita porque no esta pendiente")
    if cit_cita.puede_cancelarse is False:
        raise ValueError("No se puede cancelar esta cita")

    # Actualizar
    cit_cita.estado = "CANCELO"
    database.add(cit_cita)
    database.commit()

    # TODO: Agregar tarea en el fondo para que se envíe un mensaje vía correo electrónico

    # Entregar
    return OneCitCitaOut(
        success=True,
        message="Se ha cancelado la cita",
        data=CitCitaOut.model_validate(cit_cita),
    )


@cit_citas.post("/crear", response_model=OneCitCitaOut)
async def crear(
    current_user: Annotated[CitClienteInDB, Depends(get_current_active_user)],
    database: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    cit_cita_in: CitCitaIn,
):
    """Crear una cita"""
    if current_user.permissions.get("CIT CITAS", 0) < Permiso.CREAR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Consultar la oficina
    try:
        oficina_clave = safe_clave(cit_cita_in.oficina_clave)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No es válida la clave de la oficina")
    try:
        oficina = database.query(Oficina).filter_by(clave=oficina_clave).one()
    except (MultipleResultsFound, NoResultFound):
        return OneCitCitaOut(success=False, message="No existe esa oficina")
    if oficina.estatus != "A":
        return OneCitCitaOut(success=False, message="No está habilitada esa oficina")

    # Consultar el servicio
    try:
        cit_servicio_clave = safe_clave(cit_cita_in.cit_servicio_clave)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No es válida la clave del servicio")
    try:
        cit_servicio = database.query(CitServicio).filter_by(clave=cit_servicio_clave).one()
    except (MultipleResultsFound, NoResultFound):
        return OneCitCitaOut(success=False, message="No existe ese servicio")
    if cit_servicio.estatus != "A":
        return OneCitCitaOut(success=False, message="No está habilitado ese servicio")

    # Validar que la oficina tenga el servicio dado
    try:
        _ = (
            database.query(CitOficinaServicio)
            .filter_by(oficina_id=oficina.id)
            .filter_by(cit_servicio_id=cit_servicio.id)
            .filter_by(estatus="A")
            .one()
        )
    except NoResultFound:
        return OneCitCitaOut(success=False, message="No se puede agendar el servicio en la oficina")

    # Validar que la fecha sea un día disponible
    if cit_cita_in.fecha not in listar_dias_disponibles(database, settings):
        return OneCitCitaOut(success=False, message="No es válida la fecha")

    # Validar la hora_minuto, respecto a las horas disponibles
    if cit_cita_in.hora_minuto not in listar_horas_disponibles(database, cit_servicio, oficina, cit_cita_in.fecha):
        return OneCitCitaOut(success=False, message="No es valida la hora-minuto porque no esta disponible")

    # Definir el inicio de la cita
    inicio_dt = datetime(
        year=cit_cita_in.fecha.year,
        month=cit_cita_in.fecha.month,
        day=cit_cita_in.fecha.day,
        hour=cit_cita_in.hora_minuto.hour,
        minute=cit_cita_in.hora_minuto.minute,
    )

    # Definir el término de la cita
    termino_dt = inicio_dt + timedelta(hours=cit_servicio.duracion.hour, minutes=cit_servicio.duracion.minute)

    # Validar que la cantidad de citas de la oficina en ese tiempo NO hayan llegado al límite
    cit_citas_oficina_cantidad = (
        database.query(CitCita)
        .filter(CitCita.oficina_id == oficina.id)
        .filter(CitCita.inicio >= inicio_dt)
        .filter(CitCita.termino <= termino_dt)
        .filter(CitCita.estado != "CANCELADO")
        .filter(CitCita.estatus == "A")
        .count()
    )
    if cit_citas_oficina_cantidad >= oficina.limite_personas:
        return OneCitCitaOut(
            success=False,
            message="No se puede crear la cita porque ya se alcanzo el limite de personas en la oficina",
        )

    # Validar que la cantidad de citas PENDIENTE del cliente NO haya llegado su límite
    cit_citas_cit_cliente_cantidad = (
        database.query(CitCita)
        .filter(CitCita.cit_cliente_id == current_user.id)
        .filter(CitCita.estado == "PENDIENTE")
        .filter(CitCita.estatus == "A")
        .count()
    )
    if cit_citas_cit_cliente_cantidad >= current_user.limite_citas_pendientes:
        return OneCitCitaOut(
            success=False,
            message="No se puede crear la cita porque ya se alcanzo el limite de citas pendientes",
        )

    # Validar que el cliente no tenga una cita pendiente en la misma fecha y hora
    cit_citas_cit_cliente = (
        database.query(CitCita)
        .filter(CitCita.cit_cliente_id == current_user.id)
        .filter(CitCita.estado == "PENDIENTE")
        .filter(CitCita.inicio >= inicio_dt)
        .filter(CitCita.termino <= termino_dt)
        .filter(CitCita.estatus == "A")
        .first()
    )
    if cit_citas_cit_cliente:
        return OneCitCitaOut(
            success=False,
            message="No se puede crear la cita porque ya tiene una cita pendiente en esta fecha y hora",
        )

    # Definir cancelar_antes con 24 horas antes de la cita
    cancelar_antes = inicio_dt - timedelta(hours=24)

    # Si cancelar_antes es un dia inhábil, domingo o sábado, se busca el dia habil anterior
    cit_dias_inhabiles = database.query(CitDiaInhabil).filter_by(estatus="A").order_by(CitDiaInhabil.fecha).all()
    cit_dias_inhabiles_listado = [di.fecha for di in cit_dias_inhabiles]
    while cancelar_antes.date() in cit_dias_inhabiles_listado or cancelar_antes.weekday() == 6 or cancelar_antes.weekday() == 5:
        if cancelar_antes.date() in cit_dias_inhabiles_listado:
            cancelar_antes = cancelar_antes - timedelta(days=1)
        if cancelar_antes.weekday() == 6:  # Si es domingo, se cambia a viernes
            cancelar_antes = cancelar_antes - timedelta(days=2)
        if cancelar_antes.weekday() == 5:  # Si es sábado, se cambia a viernes
            cancelar_antes = cancelar_antes - timedelta(days=1)

    # Obtener código de acceso, entrega idAcceso (int), imagen (str), success (bool) y message (str)
    payload = {
        "aplicacion": settings.CONTROL_ACCESO_APLICACION,
        "referencia": generar_referencia(current_user.email, cit_servicio.clave, oficina.clave, inicio_dt),
        "nombres": current_user.nombres,
        "apellidos": f"{current_user.apellido_primero} {current_user.apellido_segundo}",
        "correoElectronico": current_user.email,
        "telefono": f"+52{current_user.telefono}",
        "fecha": inicio_dt.isoformat(timespec="minutes"),
        "cita": True,
    }
    try:
        respuesta = requests.post(
            url=settings.CONTROL_ACCESO_URL,
            headers={"X-Api-Key": settings.CONTROL_ACCESO_API_KEY},
            timeout=settings.CONTROL_ACCESO_TIMEOUT,
            json=payload,
        )
    except requests.exceptions.ConnectionError as error:
        return OneCitCitaOut(success=False, message=f"ERROR: No responde Control Acceso: {str(error)}")
    if respuesta.status_code != 200:
        return OneCitCitaOut(
            success=False, message=f"ERROR: No fue código 200 la respuesta de Control Acceso: {respuesta.text}"
        )
    contenido = respuesta.json()
    if contenido.get("success") is False:
        return OneCitCitaOut(
            success=False, message=f"ERROR: Falló la obtención del Código de Acceso: {contenido.get('message')}"
        )
    codigo_acceso_id = contenido.get("idAcceso")
    if not codigo_acceso_id:
        return OneCitCitaOut(success=False, message="ERROR: Faltó la idAcceso en la respuesta de Control Acceso")
    codigo_acceso_url = contenido.get("imagen")
    if not codigo_acceso_url:
        return OneCitCitaOut(success=False, message="ERROR: Faltó la imagen en la respuesta de Control Acceso")

    # Guardar
    cit_cita = CitCita(
        cit_cliente_id=current_user.id,
        cit_servicio_id=cit_servicio.id,
        oficina_id=oficina.id,
        inicio=inicio_dt,
        termino=termino_dt,
        notas=safe_string(cit_cita_in.notas, max_len=1000, save_enie=True),
        estado="PENDIENTE",
        cancelar_antes=cancelar_antes,
        asistencia=False,
        codigo_asistencia=generar_codigo_asistencia(),
        codigo_acceso_id=codigo_acceso_id,
        codigo_acceso_url=codigo_acceso_url,
    )
    database.add(cit_cita)
    database.commit()
    database.refresh(cit_cita)

    # TODO: Agregar tarea en el fondo para que se envíe un mensaje vía correo electrónico

    # Entregar
    return OneCitCitaOut(
        success=True,
        message="Se ha creado la cita",
        data=CitCitaOut.model_validate(cit_cita),
    )


@cit_citas.get("/disponibles", response_model=int)
async def disponibles(
    current_user: Annotated[CitClienteInDB, Depends(get_current_active_user)],
    database: Annotated[Session, Depends(get_db)],
):
    """Cantidad de citas disponibles"""
    if current_user.permissions.get("CIT CITAS", 0) < Permiso.VER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    # Definir la cantidad máxima de citas
    limite = LIMITE_CITAS_PENDIENTES
    if current_user.limite_citas_pendientes > LIMITE_CITAS_PENDIENTES:
        limite = current_user.limite_citas_pendientes

    # Consultar la cantidad de citas PENDIENTES del cliente
    cantidad = (
        database.query(CitCita)
        .filter(CitCita.cit_cliente_id == current_user.id)
        .filter(CitCita.estado == "PENDIENTE")
        .filter(CitCita.estatus == "A")
        .count()
    )

    # Entregar la cantidad de citas disponibles que puede agendar
    if cantidad >= limite:
        return 0
    return limite - cantidad


@cit_citas.get("/{cit_cita_id}", response_model=OneCitCitaOut)
async def detalle(
    current_user: Annotated[CitClienteInDB, Depends(get_current_active_user)],
    database: Annotated[Session, Depends(get_db)],
    cit_cita_id: str,
):
    """Detalle de una cita a partir de su ID, DEBE SER SUYA"""
    if current_user.permissions.get("CIT CITAS", 0) < Permiso.VER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    try:
        cit_cita_uuid = safe_uuid(cit_cita_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No es válida la UUID")
    cit_cita = database.query(CitCita).get(cit_cita_uuid)
    if not cit_cita:
        return OneCitCitaOut(success=False, message="No existe esa cita")
    if cit_cita.estatus != "A":
        return OneCitCitaOut(success=False, message="No está habilitada esa cita")
    if cit_cita.cit_cliente_id != current_user.id:
        return OneCitCitaOut(success=False, message="No le pertenece esa cita")
    return OneCitCitaOut(success=True, message=f"Cita {cit_cita_uuid}", data=CitCitaOut.model_validate(cit_cita))


@cit_citas.get("", response_model=CustomPage[CitCitaOut])
async def mis_citas(
    current_user: Annotated[CitClienteInDB, Depends(get_current_active_user)],
    database: Annotated[Session, Depends(get_db)],
):
    """Mis PROPIAS citas en estado PENDIENTE"""
    if current_user.permissions.get("CIT CITAS", 0) < Permiso.VER:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    consulta = database.query(CitCita).filter(CitCita.cit_cliente_id == current_user.id).filter(CitCita.estado == "PENDIENTE")
    return paginate(consulta.filter(CitCita.estatus == "A").order_by(CitCita.creado.desc()))
