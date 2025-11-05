function varargout = procesar_amortiguadores(rootDir)
% PROCESAR_AMORTIGUADORES
% Recorre archivos <masa>g_<modo>_<amortiguador>.xlsx, identifica b por archivo,
% imprime una línea por iteración y guarda promedios a Excel.

if nargin < 1 || isempty(rootDir)
    rootDir = pwd;
end

format long
m_car = 0.2124;         % masa del carrito [kg]
k_teorico = 682.87;     % constante fija para el modelo

% ===== Buscar archivos =====
pat = fullfile(rootDir, '**', '*g_*_*.xlsx');
files = dir(pat);

if isempty(files)
    warning('No se encontraron archivos con patrón "*g_*_*.xlsx" en %s', rootDir);
    if nargout>0, varargout{1} = table; end
    return
end

R = [];  % resultados por archivo

thr_eq   = 1e-4;
thr_step = 1e-3;

% Encabezado bonito para el log por iteración
fprintf('Amortiguador |   Masa (g)  |     Modo      |  b_estimate\n');
fprintf('-------------+-------------+---------------+-------------\n');

for k = 1:numel(files)
    fpath = fullfile(files(k).folder, files(k).name);

    % Parsear nombre: <masa>g_(compresion|tension)_(#).xlsx
    tok = regexp(files(k).name, '^(\d+)g_(compresion|tension)_(\d+)\.xlsx$', ...
                 'tokens', 'once', 'ignorecase');
    if isempty(tok)
        fprintf('Omitiendo: %s\n', files(k).name);
        continue;
    end

    masa_g   = str2double(tok{1});
    modo     = string(tok{2});
    amortN   = str2double(tok{3});
    m_exp    = masa_g / 1000;
    m_total  = m_car + m_exp;

    try
        % ===== Leer datos =====
        T = readtable(fpath);
        if width(T) < 2, error('El archivo no tiene al menos 2 columnas.'); end
        t = double(T{:,1});
        x = double(T{:,2});

        [t, order] = sort(t, 'ascend');
        x = x(order);
        if any(diff(t) <= 0), t = t + (0:numel(t)-1)'*eps; end
        Ts = mean(diff(t));

        % ===== Preprocesamiento =====
        x = x - x(1);
        dX = abs(diff(x));
        idx_last_move = find(dX > thr_eq, 1, 'last');
        if isempty(idx_last_move), idx_last_move = ceil(0.8*numel(x)); end
        eq = mean(x(idx_last_move:end));

        idx_step = find(dX > thr_step, 1, 'first');
        if isempty(idx_step), idx_step = max(2, ceil(0.1*numel(x))); end

        u = eq*ones(size(x));
        u(1:idx_step-1) = 0;

        % ===== Identificación (misma lógica) =====
        data = iddata(x,u,Ts);
        numerator   = 1;
        denominator = [m_total NaN k_teorico];
        sys_init = idtf(numerator,denominator);
        sys_init.Structure.Denominator.Free = [false true false];
        sys = tfest(data,sys_init);

        % ===== Normalización (misma lógica) =====
        sys_n = tf(sys.numerator/sys.numerator, sys.Denominator/sys.Numerator);
        C = m_total / sys_n.Denominator{1}(1);
        b_estimate = sys_n.Denominator{1}(2) * C;

        % ===== Métricas =====
        FitPercent = NaN; FPE = NaN; MSE = NaN;
        if isfield(sys,'Report') && isfield(sys.Report,'Fit')
            FitPercent = sys.Report.Fit.FitPercent;
            FPE = sys.Report.Fit.FPE;
            MSE = sys.Report.Fit.MSE;
        end

        % ===== Imprimir línea por iteración =====
        % Normaliza modo a "tensión/compresión" con acento para impresión
        modo_print = lower(string(modo));
        if strcmpi(modo_print,'tension'), modo_print = "tensión"; end
        if strcmpi(modo_print,'compresion'), modo_print = "compresión"; end
        fprintf('%11d | %11d | %13s | %11.4f\n', amortN, masa_g, modo_print, b_estimate);

        % ===== Guardar resultado en estructura =====
        R = [R; struct( ...
            'Amortiguador', amortN, ...
            'Masa_g', masa_g, ...
            'Modo', string(modo_print), ...
            'b_estimate', b_estimate, ...
            'FitPercent', FitPercent, ...
            'FPE', FPE, ...
            'MSE', MSE ...
        )];

    catch ME
        fprintf(2, 'Error en "%s": %s\n', files(k).name, ME.message);
        continue
    end
end

if isempty(R)
    warning('No se generaron resultados válidos.');
    if nargout>0, varargout{1} = table; end
    return
end

% ===== Agrupar y promediar para Excel =====
Tfiles = struct2table(R);
[grp, amortNums] = findgroups(Tfiles.Amortiguador);

B_mean = splitapply(@mean, Tfiles.b_estimate, grp);
B_std  = splitapply(@std,  Tfiles.b_estimate, grp);
Fit_mean = splitapply(@mean, Tfiles.FitPercent, grp);
FPE_mean = splitapply(@mean, Tfiles.FPE, grp);
MSE_mean = splitapply(@mean, Tfiles.MSE, grp);

resumen = table(amortNums, B_mean, B_std, Fit_mean, FPE_mean, MSE_mean, ...
    'VariableNames', {'Amortiguador','Valor promedio de b','DesvEst b', ...
                      'Fit promedio','FPE promedio','MSE promedio'});

% Formato y guardado
resumen = sortrows(resumen,'Amortiguador');
for v = {'Valor promedio de b','DesvEst b','Fit promedio'}
    resumen.(v{1}) = round(resumen.(v{1}),2);
end

outPath = fullfile(rootDir, 'resumen_promedios.xlsx');
writetable(resumen, outPath);
fprintf('\nResumen guardado en: %s\n', outPath);

% Mostrar FPE/MSE en notación científica al imprimir el resumen (opcional)
resumen_show = resumen;
for c = {'FPE promedio','MSE promedio'}
    resumen_show.(c{1}) = compose('%.2e', resumen.(c{1}));
end
disp(resumen_show);

if nargout>0
    varargout{1} = resumen;
end
end
