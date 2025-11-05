format long
m_car = 0.2124;
m_exp = 0.200; %Masa de 200g
m_total = m_car+m_exp;

%% Leer archivo
T = readtable("200g_tension_2.xlsx");
t = T{:,1};   % Primera columna como vector numérico
x = T{:,2};   % Segunda columna como vector numéricox = M1_X;
Ts=mean(diff(t));

%% Acercar a forma de escalón
%Centrar inicio de movimiento a origen
x=x-x(1);

%Encontrar valor de equilibrio y utilizar como entrada
dX = abs(diff(x));
idx = find(dX > 0.0001, 1, 'last');
eq = mean(x(idx:end));

% Encontrar inicio de movimiento para colocar escalón
dX = abs(diff(x));
idx = find(dX > 0.001, 1, 'first');


%Generar vector de entrada
u = eq * ones(size(x));   
u(1:idx-1) = 0;             

%% Identificación de sistema
data = iddata(x,u,Ts);

%Fijar parámetro conocido (masa)
numerator= 1;
denominator =[m_total NaN NaN];

sys_init= idtf(numerator,denominator);

sys = tfest(data,sys_init);

%% Normalizar sistema y aplicar un término de ganancia C 
sys_n = tf(sys.numerator/sys.numerator,sys.Denominator/sys.Numerator);

%% Estimar valor de resorte
C = m_total/sys_n.Denominator{1}(1);

k_estimate = sys_n.Denominator{1}(3)*C

b_estimate = sys_n.Denominator{1}(2)*C

%% Verificación
%K teórico
G = 74*10^9;
d = 0.3*10^-3;
OD = 4*10^-3;
n = 15;

k_teorico=G*d^4/(8*(OD-d)^3*n);

% Porcentaje de error

err=(k_teorico-k_estimate)/k_teorico * 100