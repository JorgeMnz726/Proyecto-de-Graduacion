function resumen = procesar_resortes(rootDir,denom)
% PROCESAR_RESORTES Recorre archivos <masa>g_<modo>_<resorte>.xlsx,
% identifica b y k por archivo y promedia por resorte.
%
% USO:
%   resumen = procesar_resortes(pwd);              % en carpeta actual
%   resumen = procesar_resortes('C:\datos\pruebas'); % otra carpeta
%
% Salida:
%   Tabla con columnas:
%   Resorte | Valor promedio de K | Valor promedio de b | Porcentaje de error medio | Fit promedio | FPE promedio | MSE promedio
%
% También guarda 'resumen_promedios.xlsx' en rootDir.

if nargin < 1 || isempty(rootDir)
    rootDir = pwd;
end

if nargin < 2 || isempty(denom)
    denom = 0.3;     % default (mm)
end

% ===== Parámetros fijos del experimento =====
format long
m_car = 0.2124;       % masa del carrito [kg]

% Parámetros para k_teorico 
G  = 74e9;            % [Pa]

switch denom
    case 0.3
        d  = 0.3e-3;  OD = 4e-3;  n = 15;
    case 0.4
        d  = 0.4e-3;  OD = 5e-3;  n = 17;
    case 0.5
        d  = 0.5e-3;  OD = 6e-3;  n = 22;
    case 0.6
        d  = 0.6e-3;  OD = 7e-3;  n = 12;
    case 0.7
        d  = 0.7e-3;  OD = 7e-3;  n = 12;
    otherwise
        error('denom no reconocido. Usa uno de: 0.3, 0.4, 0.5, 0.6, 0.7 (mm).');
end

k_teorico = G*d^4/(8*(OD - d)^3*n);

% Umbrales para detectar equilibrio y escalón 
thr_eq   = 1e-4;      % para equilibrio
thr_step = 1e-3;      % para inicio de movimiento

% ===== Buscar archivos =====
% Patrón esperado: "<masa>g_<modo>_<resorte>.xlsx"
%   <masa>   : números + 'g' (ej: 100g, 200g)
%   <modo>   : 'compresion' o 'tension'
%   <resorte>: entero (1,2,3,...)
pat = fullfile(rootDir, '**', '*g_*_*.xlsx');   % incluye subcarpetas
files = dir(pat);

if isempty(files)
    warning('No se encontraron archivos con patrón "*g_*_*.xlsx" en %s', rootDir);
    resumen = table;
    return
end

% Resultados por archivo (crecemos dinámicamente)
R = [];

for k = 1:numel(files)
    fpath = fullfile(files(k).folder, files(k).name);

    % ===== Parsear nombre de archivo =====
    % Formato:  "(\d+)g_(compresion|tension)_(\d+)\.xlsx"
    tok = regexp(files(k).name, '^(\d+)g_(compresion|tension)_(\d+)\.xlsx$', 'tokens', 'once', 'ignorecase');
    if isempty(tok)
        % Intento alterno: tolerar mayúsculas o espacios
        tok = regexp(lower(files(k).name), '^(\d+)g_(compresion|tension)_(\d+)\.xlsx$', 'tokens', 'once');
    end
    if isempty(tok)
        % Si no coincide el patrón, saltamos el archivo
        fprintf('Omitiendo (nombre no coincide): %s\n', files(k).name);
        continue;
    end

    masa_g   = str2double(tok{1});     % masa en gramos desde el nombre
    modo     = string(tok{2});
    resorteN = str2double(tok{3});

    % Masa total = carrito + masa experimental del archivo
    m_exp   = masa_g/1000;             % [kg]
    m_total = m_car + m_exp;           % [kg]

    try
        % ===== Leer datos =====
        T = readtable(fpath);
        if width(T) < 2
            error('El archivo no tiene al menos 2 columnas (tiempo, posicion).');
        end
        t = T{:,1};
        x = T{:,2};

        % Asegurar numeric column vectors
        t = double(t(:));
        x = double(x(:));

        % Ordenar por tiempo en caso de desorden
        [t, order] = sort(t, 'ascend');
        x = x(order);

        % Corregir tiempos duplicados mínimos 
        dt = diff(t);
        if any(dt <= 0)
            % Perturbar mínimamente
            t = t + (0:numel(t)-1)'*eps;
        end

        % Periodo de muestreo promedio
        Ts = mean(diff(t));

        % ===== Preprocesamiento (entrada escalón) =====
        % Centrar inicio de movimiento a origen
        x = x - x(1);

        % Equilibrio (promedio del tramo final estable)
        dX  = abs(diff(x));
        idx_last_move = find(dX > thr_eq, 1, 'last');
        if isempty(idx_last_move)
            idx_last_move = ceil(0.8*numel(x)); % fallback
        end
        eq  = mean(x(idx_last_move:end));

        % Inicio del movimiento para escalón
        dX  = abs(diff(x));
        idx_step = find(dX > thr_step, 1, 'first');
        if isempty(idx_step)
            idx_step = max(2, ceil(0.1*numel(x))); % fallback prudente
        end

        % Generar entrada u(t): paso de magnitud 'eq' a partir de idx_step
        u = eq*ones(size(x));
        u(1:idx_step-1) = 0;

        % ===== Identificación =====
        data = iddata(x, u, Ts);

        % Modelo continuo 2º orden con m fijo:
        % G(s) = 1 / (m s^2 + b s + k)
        numerator   = 1;
        denominator = [m_total NaN NaN];
        sys_init    = idtf(numerator, denominator);

        sys = tfest(data, sys_init);   % requiere System Identification Toolbox

        % Normalizar sistema y aplicar un término de ganancia C
        sys_n = tf(sys.numerator/sys.numerator,sys.Denominator/sys.Numerator);
        C = m_total/sys_n.Denominator{1}(1);
        

        % ===== Extraer parámetros =====
        k_estimate = sys_n.Denominator{1}(3)*C;
        b_estimate = sys_n.Denominator{1}(2)*C;

        % Métricas de ajuste (pueden estar en el Report)
        FitPercent = sys.Report.Fit.FitPercent;
        FPE        = sys.Report.Fit.FPE;
        MSE        = sys.Report.Fit.MSE;
        if isfield(sys,'Report') && isfield(sys.Report,'Fit')
            if isfield(sys.Report.Fit,'FitPercent'), FitPercent = sys.Report.Fit.FitPercent; end
            if isfield(sys.Report.Fit,'FPE'),        FPE        = sys.Report.Fit.FPE;         end
            if isfield(sys.Report.Fit,'MSE'),        MSE        = sys.Report.Fit.MSE;         end
        end

        % Error porcentual respecto a k_teorico (mismo para todos en esta versión)
        err_pct = (k_teorico - k_estimate)/k_teorico*100;

        % Guardar registro por archivo
        R = [R; struct( ...
            'Archivo',          string(files(k).name), ...
            'Masa_g',           masa_g, ...
            'Modo',             modo, ...
            'Resorte',          resorteN, ...
            'k_estimate',       k_estimate, ...
            'b_estimate',       b_estimate, ...
            'Error_pct',        err_pct, ...
            'FitPercent',       FitPercent, ...
            'FPE',              FPE, ...
            'MSE',              MSE  ...
        )];

    catch ME
        fprintf(2, 'Error en "%s": %s\n', files(k).name, ME.message);
        % Continúa con el siguiente archivo
        continue
    end
end

if isempty(R)
    warning('No se generaron resultados. Revisa los archivos o el patrón de nombres.');
    resumen = table;
    return
end

% ===== Convertir a tabla y promediar por Resorte =====
Tfiles = struct2table(R);

% Promedios por resorte
[grp, resNums] = findgroups(Tfiles.Resorte);

K_mean   = splitapply(@mean, Tfiles.k_estimate, grp);
B_mean   = splitapply(@mean, Tfiles.b_estimate, grp);
Err_mean = splitapply(@mean, Tfiles.Error_pct,  grp);
Fit_mean = splitapply(@mean, Tfiles.FitPercent, grp);
FPE_mean = splitapply(@mean, Tfiles.FPE,        grp);
MSE_mean = splitapply(@mean, Tfiles.MSE,        grp);

resumen = table( ...
    resNums, K_mean, B_mean, Err_mean, Fit_mean, FPE_mean, MSE_mean, ...
    'VariableNames', {'Resorte', 'Valor promedio de K', 'Valor promedio de b', ...
                      'Porcentaje de error medio', 'Fit promedio', 'FPE promedio', 'MSE promedio'} );

% Ordenar por número de resorte
resumen = sortrows(resumen, 'Resorte');

% === (A) Guardar limpio (numérico) ===

vars2 = {'Valor promedio de K','Valor promedio de b','Porcentaje de error medio','Fit promedio'};
for v = vars2
    if ismember(v{1}, resumen.Properties.VariableNames)
        resumen.(v{1}) = round(resumen.(v{1}), 2);
    end
end

% Ordenar y GUARDAR (numérico)
resumen = sortrows(resumen, 'Resorte');
outPath = fullfile(rootDir, 'resumen_promedios.xlsx');
writetable(resumen, outPath);
fprintf('Resumen guardado en: %s\n', outPath);

% === (B) Mostrar bonito en consola ===
% - Copia para DISPLAY: convierte FPE/MSE a strings '%.2e'
resumen_show = resumen;
colsSci = {'FPE promedio','MSE promedio'};
for c = colsSci
    if ismember(c{1}, resumen_show.Properties.VariableNames)
        resumen_show.(c{1}) = compose('%.2e', resumen.(c{1}));
    end
end

disp(resumen_show);
