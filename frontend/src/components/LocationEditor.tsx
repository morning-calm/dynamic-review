import { useEffect, useMemo, useState } from 'react';
import { toast } from 'react-toastify';
import { api, ApiError, type FinalLocation, type FinalLocationModel, type MenuPin } from '../api';

const errText = (e: unknown, fallback: string): string =>
  e instanceof ApiError ? e.detail || e.code : fallback;

/** The VR map panel (MapPanelController.ResizePanel) sizes the ROOT rect so the
 * sprite's LONG axis spans 18 units (maxMapSize) — but the pins' parent is the
 * map Image INSIDE MapPanel.prefab's MapMask, a stretched child with sizeDelta
 * (−3.5, −3). So the image rect the anchoredPosition offsets address is
 * (rootW − 3.5) × (rootH − 3) units, origin top-left, +x right, −y down, and
 * the sprite is stretched (preserveAspect off) to fill exactly that rect.
 * Verified against known pins on all 8 maps (2026-08-22). */
const MAP_UNITS = 18;
const MASK_INSET_X = 3.5;
const MASK_INSET_Y = 3;
/** Units the map image spans on each axis for a sprite of natural size w×h. */
const mapUnitSize = (w: number, h: number) => {
  const ratio = w / h;
  const rootW = ratio >= 1 ? MAP_UNITS : MAP_UNITS * ratio;
  const rootH = ratio >= 1 ? MAP_UNITS / ratio : MAP_UNITS;
  return { uw: rootW - MASK_INSET_X, uh: rootH - MASK_INSET_Y };
};
/** Pin rect drawn to scale (~0.59 × 0.62 units in the headset) so overlap shows. */
const PIN_W = 0.59;
const PIN_H = 0.62;

/** 2D pin placer over the committed country map PNG (frontend/public/maps/,
 * copied from the Unity checkout — the SAME sprites the headset bends through
 * CurvedUI, which only affects rendering, not coordinates). Click to move THIS
 * location's pin; other pins show gray for context. */
const PinPlacer = ({
  mapName,
  locId,
  pins,
  extraButtons,
  draft,
  onPlace,
}: {
  mapName: string;
  locId: string;
  pins: MenuPin[];
  extraButtons: MenuPin[];
  draft: { x: number; y: number } | null;
  onPlace: (x: number, y: number) => void;
}) => {
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    setNat(null);
    setMissing(false);
  }, [mapName]);

  if (!mapName) {
    return (
      <p className="text-xs text-amber-300">
        The selected menu has no <code>MapName</code> — nothing to place a pin on.
      </p>
    );
  }
  if (missing) {
    return (
      <p className="text-xs text-amber-300">
        No committed map image for <code>{mapName}</code> (expected{' '}
        <code>frontend/public/maps/{mapName}.png</code>, copied from the Unity checkout).
      </p>
    );
  }

  const units = nat ? mapUnitSize(nat.w, nat.h) : null;
  const toPct = (xUnits: number, yUnits: number) =>
    units
      ? {
          left: `${(xUnits / units.uw) * 100}%`,
          top: `${(-yUnits / units.uh) * 100}%`,
        }
      : { left: '0%', top: '0%' };
  const pinSize = units
    ? { width: `${(PIN_W / units.uw) * 100}%`, height: `${(PIN_H / units.uh) * 100}%` }
    : { width: '0%', height: '0%' };

  const marker = (
    p: { x: number; y: number },
    key: string,
    label: string,
    cls: string,
    title: string,
    // anchoredPosition places the prefab's PIVOT at (x, −y): MapPinButton's root
    // pivot is (0.5, 0.5) → the coordinate is the pin's CENTER; MapExtraButton's
    // is (0.5, 0) → bottom-center (Unity y-up). Translate accordingly or every
    // marker renders half a pin down-right of its true headset position.
    pivot: 'center' | 'bottom' = 'center',
  ) => (
    <div
      key={key}
      className={`pointer-events-none absolute flex items-center justify-center rounded-sm border text-[8px] leading-none ${cls}`}
      style={{
        ...toPct(p.x, p.y),
        ...pinSize,
        transform: pivot === 'center' ? 'translate(-50%, -50%)' : 'translate(-50%, -100%)',
      }}
      title={title}
    >
      <span className="truncate px-0.5">{label}</span>
    </div>
  );

  return (
    <div className="relative inline-block max-w-full">
      <img
        src={`/maps/${mapName}.png`}
        alt={mapName}
        className="block max-w-full rounded border border-gray-700"
        onLoad={(e) =>
          setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })
        }
        onError={() => setMissing(true)}
        onClick={(e) => {
          if (!units) return;
          const rect = e.currentTarget.getBoundingClientRect();
          const fx = (e.clientX - rect.left) / rect.width;
          const fy = (e.clientY - rect.top) / rect.height;
          onPlace(
            Math.round(fx * units.uw * 1000) / 1000,
            Math.round(-fy * units.uh * 1000) / 1000,
          );
        }}
        style={{ cursor: nat ? 'crosshair' : 'wait' }}
      />
      {nat && (
        <>
          {pins
            .filter((p) => p.LocationId !== locId)
            .map((p) =>
              marker(
                { x: p.xPos, y: p.yPos },
                `pin-${p.LocationId}`,
                p.LocationId,
                'border-gray-500 bg-gray-800/70 text-gray-300',
                `${p.LocationId} (${p.xPos}, ${p.yPos})`,
              ),
            )}
          {extraButtons.map((p) =>
            marker(
              { x: p.xPos, y: p.yPos },
              `xb-${p.LocationId}`,
              p.LocationId,
              'border-amber-700 bg-amber-900/50 text-amber-200',
              `${p.LocationId} — ExtraMapButtons (off-map series button)`,
              'bottom',
            ),
          )}
          {draft &&
            marker(
              draft,
              'draft',
              locId,
              'border-indigo-400 bg-indigo-600/70 font-semibold text-white',
              `${locId} (${draft.x}, ${draft.y}) — unsaved`,
            )}
        </>
      )}
    </div>
  );
};

/** One TripLocation's editor: title key / skybox / tile order + the pin placer.
 * Every save is a targeted STAGING write; production follows only at publish. */
const LocationCard = ({
  tripId,
  loc,
  model,
  onSaved,
}: {
  tripId: string;
  loc: FinalLocation;
  model: FinalLocationModel;
  onSaved: () => void;
}) => {
  const [titleKey, setTitleKey] = useState(loc.locationTitleKey);
  const [skybox, setSkybox] = useState(loc.skyboxTextureId);
  const [order, setOrder] = useState<string[]>(loc.trips);
  const [menuId, setMenuId] = useState(loc.pin?.menu_id ?? model.menus[0]?.id ?? '');
  const [draftPin, setDraftPin] = useState<{ x: number; y: number } | null>(
    loc.pin ? { x: loc.pin.x, y: loc.pin.y } : null,
  );
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setTitleKey(loc.locationTitleKey);
    setSkybox(loc.skyboxTextureId);
    setOrder(loc.trips);
    setDraftPin(loc.pin ? { x: loc.pin.x, y: loc.pin.y } : null);
    if (loc.pin) setMenuId(loc.pin.menu_id);
  }, [loc]);

  const menu = model.menus.find((m) => m.id === menuId);
  const manifest = useMemo(
    () => new Set(model.skyboxes.manifest.map((s) => s.toLowerCase())),
    [model.skyboxes.manifest],
  );
  const skyboxUnknown =
    skybox.trim() !== '' && manifest.size > 0 && !manifest.has(skybox.trim().toLowerCase());

  const fieldsDirty =
    titleKey.trim() !== loc.locationTitleKey || skybox.trim() !== loc.skyboxTextureId;
  const orderDirty = order.join('\u0000') !== loc.trips.join('\u0000');
  const pinDirty =
    draftPin !== null &&
    (loc.pin === null ||
      loc.pin.menu_id !== menuId ||
      loc.pin.x !== draftPin.x ||
      loc.pin.y !== draftPin.y);

  const save = (body: Parameters<typeof api.saveFinalLocation>[1], done: string) => {
    setBusy(true);
    api
      .saveFinalLocation(tripId, body)
      .then(() => {
        toast.success(done);
        onSaved();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Save failed')))
      .finally(() => setBusy(false));
  };

  const move = (i: number, d: -1 | 1) => {
    const j = i + d;
    if (j < 0 || j >= order.length) return;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    setOrder(next);
  };

  const savePin = () => {
    if (!draftPin || !menuId) return;
    setBusy(true);
    api
      .saveFinalPin(tripId, { loc_id: loc.id, menu_id: menuId, x: draftPin.x, y: draftPin.y })
      .then((r) => {
        toast.success(
          `Pin saved to staging ${r.menu_id}.${r.field} — production follows at publish.`,
        );
        onSaved();
      })
      .catch((e: unknown) => toast.error(errText(e, 'Pin save failed')))
      .finally(() => setBusy(false));
  };

  return (
    <div className="space-y-4 rounded border border-gray-700 bg-gray-900/40 p-3">
      <p className="text-xs text-gray-400">
        <span className="font-semibold text-gray-200">TripLocations/{loc.id}</span>
        {loc.locationCountry && ` · ${loc.locationCountry}`}
        {loc.locationName && ` · ${loc.locationName}`}
      </p>

      {/* title key + skybox */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs text-gray-400">
          locationTitleKey
          <input
            value={titleKey}
            onChange={(e) => setTitleKey(e.target.value)}
            className="mt-1 block w-48 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-sm text-gray-100"
          />
        </label>
        <label className="text-xs text-gray-400">
          skyboxTextureId
          <input
            list="skybox-options"
            value={skybox}
            onChange={(e) => setSkybox(e.target.value)}
            className="mt-1 block w-64 rounded border border-gray-600 bg-gray-900 px-2 py-1 text-sm text-gray-100"
          />
        </label>
        <datalist id="skybox-options">
          {model.skyboxes.used.map((s) => (
            <option key={`u-${s.id}`} value={s.id}>{`in use ×${s.count}`}</option>
          ))}
          {model.skyboxes.manifest
            .filter((s) => !model.skyboxes.used.some((u) => u.id === s))
            .map((s) => (
              <option key={`m-${s}`} value={s} />
            ))}
        </datalist>
        <button
          type="button"
          disabled={busy || !fieldsDirty}
          onClick={() =>
            save(
              { loc_id: loc.id, locationTitleKey: titleKey.trim(), skyboxTextureId: skybox.trim() },
              'TripLocation fields written to staging',
            )
          }
          className="rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          Save fields
        </button>
      </div>
      {skyboxUnknown && (
        <p className="text-xs text-amber-300">
          ⚠ “{skybox.trim()}” is not in the shipped-skybox manifest (
          {model.skyboxes.manifest.length} known
          {model.skyboxes.manifest_generated_at
            ? `, exported ${model.skyboxes.manifest_generated_at.slice(0, 10)}`
            : ''}
          ) — the headset would show the default sky until the texture reaches S3{' '}
          <code>360_Skyboxes/</code>. Saving is allowed, not blocked.
        </p>
      )}

      {/* tile order */}
      <div>
        <p className="mb-1 text-[11px] uppercase tracking-wide text-gray-500">
          Tile order (trips[] — order = button order in the headset)
        </p>
        <ul className="space-y-1">
          {order.map((g, i) => {
            const meta = loc.groups.find((x) => x.tg_id === g);
            return (
              <li key={g} className="flex items-center gap-2 text-sm">
                <span className="w-5 text-right text-xs tabular-nums text-gray-500">{i + 1}.</span>
                <span
                  className={
                    meta?.is_this_family ? 'font-semibold text-indigo-300' : 'text-gray-200'
                  }
                >
                  {g}
                </span>
                {meta && !meta.exists && (
                  <span className="rounded bg-rose-900/60 px-1 text-[10px] uppercase text-rose-200">
                    no staging doc
                  </span>
                )}
                <span className="ml-auto flex gap-1">
                  <button
                    type="button"
                    onClick={() => move(i, -1)}
                    disabled={i === 0}
                    aria-label={`Move ${g} up`}
                    className="rounded border border-gray-600 px-1.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-30"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    onClick={() => move(i, 1)}
                    disabled={i === order.length - 1}
                    aria-label={`Move ${g} down`}
                    className="rounded border border-gray-600 px-1.5 text-xs text-gray-300 hover:bg-gray-700 disabled:opacity-30"
                  >
                    ↓
                  </button>
                </span>
              </li>
            );
          })}
        </ul>
        <button
          type="button"
          disabled={busy || !orderDirty}
          onClick={() => save({ loc_id: loc.id, trips: order }, 'Tile order written to staging')}
          className="mt-2 rounded bg-custom-green px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          Save order
        </button>
      </div>

      {/* pin placer */}
      <div>
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <p className="text-[11px] uppercase tracking-wide text-gray-500">
            Map pin — click the map to place, then save
          </p>
          <select
            value={menuId}
            onChange={(e) => setMenuId(e.target.value)}
            className="rounded border border-gray-600 bg-gray-900 px-2 py-0.5 text-xs text-gray-100"
          >
            {model.menus.map((m) => (
              <option key={m.id} value={m.id}>
                {m.id}
                {m.map_name ? ` (${m.map_name})` : ''}
              </option>
            ))}
          </select>
          {loc.pin === null && (
            <span className="rounded bg-amber-900/60 px-1.5 py-0.5 text-[10px] uppercase text-amber-200">
              no pin yet — location unreachable on the map
            </span>
          )}
          <button
            type="button"
            disabled={busy || !pinDirty}
            onClick={savePin}
            className="rounded bg-custom-green px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            Save pin to staging
          </button>
          {draftPin && (
            <span className="text-xs tabular-nums text-gray-400">
              x {draftPin.x} · y {draftPin.y}
            </span>
          )}
        </div>
        {menu ? (
          <PinPlacer
            mapName={menu.map_name}
            locId={loc.id}
            pins={menu.pins}
            extraButtons={menu.extra_buttons}
            draft={draftPin}
            onPlace={(x, y) => setDraftPin({ x, y })}
          />
        ) : (
          <p className="text-xs text-gray-500">No *_Trip_Menu docs found on staging.</p>
        )}
      </div>
    </div>
  );
};

/** Check-4 body: every staging TripLocation listing this family. */
const LocationEditor = ({ tripId }: { tripId: string }) => {
  const [model, setModel] = useState<FinalLocationModel | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api
      .getFinalLocation(tripId)
      .then(setModel)
      .catch((e: unknown) => setError(errText(e, 'Failed to load TripLocation data')));
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(load, [tripId]);

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!model) return <p className="text-xs text-gray-500">Loading TripLocation data…</p>;
  if (model.locations.length === 0) {
    return (
      <p className="text-xs text-amber-300">
        This family ({model.tg_id}) is in NO staging TripLocation — it will not appear on any
        map tile. Create/assign the location via the pipeline (09c) first.
      </p>
    );
  }
  return (
    <div className="space-y-4">
      {model.locations.map((loc) => (
        <LocationCard key={loc.id} tripId={tripId} loc={loc} model={model} onSaved={load} />
      ))}
    </div>
  );
};

export default LocationEditor;
